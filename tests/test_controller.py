import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "plugins/task-router/skills/task-router/scripts"))
from router_core import choose, digest
from task_router_runtime.controller import Controller, outcome
from task_router_runtime.store import StateError, Store


def config():
    return {"schema_version": 2, "dispatcher": {"model_id": "first"},
            "profiles": {"a": {"model_id": "first", "enabled": True, "reasoning_effort": "low"},
                         "b": {"model_id": "second", "enabled": True, "reasoning_effort": "high"}},
            "roles": {"coding": ["a", "b"]}, "tasks": {"edit": {"role": "coding"}},
            "fallback": {"inline": True}, "max_attempts": 2}


class Adapter:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        step = self.steps.pop(0)
        return step(kwargs) if callable(step) else step


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.cwd = root / "workspace"
        self.cwd.mkdir()
        self.directory = root / "state"
        self.store = Store(self.directory)
        self.addCleanup(lambda: self.store.close())

    def payload(self, **updates):
        policy = config()
        data = {"task": "edit", "cwd": str(self.cwd), "prompt": "Complete the file", "writable": True,
                "timeout": 60, "verify": None, "policy": policy, "catalog": None,
                "provider": "test", "config_hash": digest(policy)}
        data.update(updates)
        return data

    def submit(self, **updates):
        return self.store.submit(self.payload(**updates))[0]

    def test_failed_model_switches_without_an_llm_router_call(self):
        adapter = Adapter([outcome("failed", "availability", "simulated 503", True),
                           outcome("succeeded", text="done")])
        task = self.submit()
        result = Controller(self.store, adapter, choose).execute(task)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual([call["model"] for call in adapter.calls], ["first", "second"])
        self.assertEqual([a["number"] for a in self.store.attempts(task)], [1, 2])

    def test_partial_edit_continues_instead_of_replaying_after_store_reopen(self):
        marker = self.cwd / "progress.txt"

        def partial(call):
            marker.write_text("step one\n")
            return outcome("failed", "availability", "simulated service failure after partial edit", True)

        task = self.submit()
        first = Controller(self.store, Adapter([partial]), choose).execute(task, one_attempt=True)
        self.assertEqual(first["status"], "retrying")
        deadline = first["deadline"]
        self.store.close()
        self.store = Store(self.directory)

        def finish(call):
            self.assertEqual(marker.read_text(), "step one\n")
            self.assertIn("progress.txt", call["prompt"])
            self.assertIn("do not repeat completed side effects", call["prompt"])
            marker.write_text(marker.read_text() + "step two\n")
            return outcome("succeeded", text="continued")

        final = Controller(self.store, Adapter([finish]), choose).execute(task)
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(marker.read_text().count("step one"), 1)
        self.assertEqual(final["deadline"], deadline)

    def test_duplicate_submission_and_duplicate_execution_do_not_rerun(self):
        payload = self.payload()
        task, created = self.store.submit(payload, "request-one")
        again, duplicate = self.store.submit(payload, "request-one")
        self.assertEqual(task, again)
        self.assertTrue(created)
        self.assertFalse(duplicate)
        adapter = Adapter([outcome("succeeded")])
        runner = Controller(self.store, adapter, choose)
        runner.execute(task)
        runner.execute(task)
        self.assertEqual(len(adapter.calls), 1)
        with self.assertRaises(StateError):
            self.store.submit(self.payload(prompt="different"), "request-one")

    def test_nonterminal_timeout_cannot_spawn_replacement(self):
        adapter = Adapter([outcome("unknown", "timeout", quiescent=False)])
        task = self.submit()
        first = Controller(self.store, adapter, choose).execute(task)
        self.assertEqual(first["status"], "unknown")
        final = Controller(self.store, adapter, choose).execute(task)
        self.assertEqual(final["status"], "unknown")
        self.assertEqual(len(adapter.calls), 1)
        other = self.submit(prompt="overlapping task")
        with self.assertRaises(StateError):
            Controller(self.store, adapter, choose).execute(other)

    def test_crash_during_attempt_remains_unknown_after_restart(self):
        task = self.submit()
        self.store.start_attempt(task, "first", "low", {"files": {}, "incomplete": False})
        self.store.close()
        self.store = Store(self.directory)
        adapter = Adapter([])
        result = Controller(self.store, adapter, choose).execute(task)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(adapter.calls)

    def test_auth_error_does_not_cycle_through_models(self):
        task = self.submit()
        adapter = Adapter([outcome("failed", "auth", "authentication failure")])
        self.assertEqual(Controller(self.store, adapter, choose).execute(task)["status"], "failed")
        self.assertEqual(len(adapter.calls), 1)

    def test_budget_survives_resume_and_all_candidates_failed(self):
        task = self.submit()
        adapter = Adapter([outcome("failed", "availability", retryable=True),
                           outcome("failed", "availability", retryable=True)])
        runner = Controller(self.store, adapter, choose)
        self.assertEqual(runner.execute(task, one_attempt=True)["status"], "retrying")
        result = runner.execute(task)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["message"], "attempt_budget_exhausted")
        self.assertEqual(len(adapter.calls), 2)

    def test_expired_task_does_not_reset_time_budget(self):
        task = self.submit()
        with self.store.db:
            self.store.db.execute("UPDATE tasks SET deadline=? WHERE id=?", (time.time() - 1, task))
        adapter = Adapter([])
        result = Controller(self.store, adapter, choose).execute(task)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(adapter.calls)

    def test_configuration_is_frozen_after_submission(self):
        payload = self.payload()
        task = self.store.submit(payload)[0]
        payload["policy"]["profiles"]["a"]["model_id"] = "changed"
        adapter = Adapter([outcome("succeeded")])
        Controller(self.store, adapter, choose).execute(task)
        self.assertEqual(adapter.calls[0]["model"], "first")

    def test_cancel_queued_task_never_calls_model(self):
        task = self.submit()
        self.store.cancel(task)
        adapter = Adapter([])
        self.assertEqual(Controller(self.store, adapter, choose).execute(task)["status"], "cancelled")
        self.assertFalse(adapter.calls)

    def test_signal_requests_cancel_via_adapter(self):
        task = self.submit()
        signal_state = [False]

        def step(call):
            signal_state[0] = True
            self.assertTrue(call["cancelled"]())
            return outcome("cancelled", "cancelled")

        result = Controller(self.store, Adapter([step]), choose, lambda: signal_state[0]).execute(task)
        self.assertEqual(result["status"], "cancelled")

    def test_verifier_detects_model_claim_without_actual_fix(self):
        task = self.submit(verify=[sys.executable, "-c", "raise SystemExit(7)"])
        result = Controller(self.store, Adapter([outcome("succeeded", text="all fixed")]), choose).execute(task)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["result"]["error_kind"], "verification")
        self.assertEqual(result["result"]["verification"]["exit_code"], 7)

    def test_success_is_saved_before_caller_restarts(self):
        task = self.submit(verify=[sys.executable, "-c", "print('checked')"])
        result = Controller(self.store, Adapter([outcome("succeeded")]), choose).execute(task)
        self.assertTrue(result["result"]["completion_verified"])
        self.store.close()
        self.store = Store(self.directory)
        adapter = Adapter([])
        resumed = Controller(self.store, adapter, choose).execute(task)
        self.assertEqual(resumed["status"], "succeeded")
        self.assertFalse(adapter.calls)

    def test_bad_adapter_outcome_is_not_trusted(self):
        task = self.submit()
        result = Controller(self.store, Adapter([{"status": "succeeded"}]), choose).execute(task)
        self.assertEqual(result["status"], "unknown")

    def test_state_directory_cannot_be_shared_with_other_users(self):
        other = self.directory.parent / "public-state"
        other.mkdir(mode=0o755)
        with self.assertRaises(StateError):
            Store(other)

    def test_another_execution_lock_prevents_double_writer(self):
        task = self.submit()
        other = Store(self.directory)
        try:
            with other.execution_lock(), self.assertRaises(StateError):
                Controller(self.store, Adapter([]), choose).execute(task)
        finally:
            other.close()

    def test_readonly_scope_violation_is_reported(self):
        task = self.submit(writable=False)

        def bad(call):
            (self.cwd / "unexpected.txt").write_text("changed")
            return outcome("succeeded")

        result = Controller(self.store, Adapter([bad]), choose).execute(task)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["result"]["error_kind"], "scope_violation")

    def test_readonly_parent_directory_ignores_controller_bookkeeping(self):
        task = self.submit(writable=False, cwd=str(self.directory.parent))
        result = Controller(self.store, Adapter([outcome("succeeded")]), choose).execute(task)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["changed_files"], [])

    def test_unavailable_candidate_pool_does_not_start_process(self):
        policy = config()
        for profile in policy["profiles"].values():
            profile["enabled"] = False
        task = self.submit(policy=policy)
        adapter = Adapter([])
        result = Controller(self.store, adapter, choose).execute(task)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempts"], [])
        self.assertFalse(adapter.calls)

    def test_retry_checkpoint_reserves_workspace_until_resumed_or_cancelled(self):
        first = self.submit()

        def partial(call):
            (self.cwd / "partial.txt").write_text("preserve this\n")
            return outcome("failed", "availability", retryable=True)

        Controller(self.store, Adapter([partial]), choose).execute(first, one_attempt=True)
        second = self.submit(prompt="another task")
        adapter = Adapter([outcome("succeeded")])
        with self.assertRaises(StateError):
            Controller(self.store, adapter, choose).execute(second)
        self.assertFalse(adapter.calls)
        self.store.cancel(first)
        self.assertEqual(Controller(self.store, adapter, choose).execute(second)["status"], "succeeded")
        self.assertEqual((self.cwd / "partial.txt").read_text(), "preserve this\n")


class RunnerCommandTests(unittest.TestCase):
    def test_submit_survives_process_restart_and_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            workspace = root / "work"
            workspace.mkdir()
            policy_path = root / "routing.json"
            policy_path.write_text(json.dumps(config()))
            command = [sys.executable, "-B", str(ROOT / "scripts/run_task.py"), "--state-dir", str(root / "state")]
            submitted = subprocess.run(command + ["submit", "--task", "edit", "--cwd", str(workspace),
                                                  "--config", str(policy_path), "--write", "--prompt", "test"],
                                       capture_output=True, text=True)
            self.assertEqual(submitted.returncode, 0, submitted.stdout + submitted.stderr)
            task = json.loads(submitted.stdout)["task_id"]
            checked = subprocess.run(command + ["status", task], capture_output=True, text=True)
            self.assertEqual(json.loads(checked.stdout)["status"], "queued")
            cancelled = subprocess.run(command + ["cancel", task], capture_output=True, text=True)
            self.assertEqual(json.loads(cancelled.stdout)["status"], "cancelled")


class ProcessRecoveryTests(unittest.TestCase):
    setUp = ControllerTests.setUp
    payload = ControllerTests.payload
    submit = ControllerTests.submit

    def command_environment(self, scenario):
        binary = self.directory.parent / "bin"
        binary.mkdir(exist_ok=True)
        shutil.copy2(ROOT / "tests/fixtures/fake_app_server.py", binary / "codex")
        self.log = self.directory.parent / "calls.jsonl"
        environment = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ.get("PATH", ""),
                       "TASK_ROUTER_FAKE_SCENARIO": scenario, "TASK_ROUTER_FAKE_LOG": str(self.log),
                       "PYTHONDONTWRITEBYTECODE": "1"}
        return [sys.executable, "-B", str(ROOT / "scripts/run_task.py"), "--state-dir", str(self.directory)], environment

    def test_cli_restart_continues_partial_write_and_does_not_repeat_success(self):
        task = self.submit(verify=[sys.executable, "-c", "from pathlib import Path; assert Path('progress.txt').read_text() == 'step one\\nstep two\\n'"])
        command, environment = self.command_environment("partial")
        first = subprocess.run(command + ["resume", task, "--one-attempt"], env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(first.returncode, 75, first.stdout + first.stderr)
        self.assertEqual((self.cwd / "progress.txt").read_text(), "step one\n")
        second = subprocess.run(command + ["resume", task], env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertTrue(json.loads(second.stdout)["result"]["completion_verified"])
        third = subprocess.run(command + ["resume", task], env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
        self.assertEqual([json.loads(line)["model"] for line in self.log.read_text().splitlines()], ["first", "second"])

    def await_turn(self, task, process):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and process.poll() is None:
            attempts = self.store.attempts(task)
            if attempts and attempts[-1]["turn_id"]:
                return attempts[-1]
            time.sleep(0.02)
        self.fail("fixture turn did not start")

    def test_cancel_from_another_process_interrupts_the_active_turn(self):
        task = self.submit()
        command, environment = self.command_environment("timeout")
        process = subprocess.Popen(command + ["resume", task], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.await_turn(task, process)
            request = subprocess.run(command + ["cancel", task], env=environment, capture_output=True, text=True, timeout=5)
            self.assertEqual(request.returncode, 0)
            output, error = process.communicate(timeout=6)
            self.assertEqual(process.returncode, 130, output + error)
            self.assertEqual(json.loads(output)["status"], "cancelled")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_killed_controller_does_not_replay_an_unconfirmed_turn(self):
        task = self.submit()
        command, environment = self.command_environment("hang")
        process = subprocess.Popen(command + ["resume", task], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        attempt = None
        try:
            attempt = self.await_turn(task, process)
            process.kill()
            process.communicate(timeout=5)
            resumed = subprocess.run(command + ["resume", task], env=environment, capture_output=True, text=True, timeout=5)
            self.assertEqual(resumed.returncode, 4, resumed.stdout + resumed.stderr)
            self.assertEqual(json.loads(resumed.stdout)["status"], "unknown")
            self.assertEqual(len(self.store.attempts(task)), 1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if attempt:
                try:
                    os.killpg(attempt["pgid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    unittest.main()
