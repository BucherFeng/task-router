import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/task-router/scripts"))
from task_router_runtime.codex_adapter import CodexAdapter, error_kind, safe_message
from task_router_runtime.store import process_present


class AdapterTests(unittest.TestCase):
    def run_case(self, scenario, timeout=5, callback=None, cancel=None):
        events = []
        with tempfile.TemporaryDirectory() as cwd, patch.dict(os.environ, {"TASK_ROUTER_FAKE_SCENARIO": scenario}):
            result = CodexAdapter(str(ROOT / "tests/fixtures/fake_app_server.py")).run(
                prompt="bounded fixture", cwd=cwd, model="first", effort="low", provider="fixture", writable=True,
                timeout=timeout, on_event=callback or events.append, cancelled=cancel or (lambda: False))
        if events:
            self.assertFalse(process_present(events[0]["pid"]))
        return result, events

    def test_success_with_tool_items(self):
        result, events = self.run_case("success")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result_text"], "fixture done")
        self.assertTrue(result["quiescent"])
        self.assertEqual([e["kind"] for e in events], ["process", "thread", "turn"])

    def test_will_retry_is_not_an_external_retry(self):
        result, events = self.run_case("will_retry")
        self.assertEqual(result["status"], "succeeded")
        self.assertIn("server_retry", [e["kind"] for e in events])

    def test_structured_503_terminal_is_retryable(self):
        result, _ = self.run_case("service_failure")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["retryable"])
        self.assertTrue(result["quiescent"])

    def test_auth_is_not_retryable(self):
        result, _ = self.run_case("auth")
        self.assertEqual(result["error_kind"], "auth")
        self.assertFalse(result["retryable"])

    def test_lost_start_ack_and_eof_cannot_be_replayed(self):
        for case in ("lost_ack", "eof"):
            with self.subTest(case=case):
                result, _ = self.run_case(case)
                self.assertEqual(result["status"], "unknown")
                self.assertFalse(result["quiescent"])

    def test_terminal_with_unfinished_tool_is_unknown(self):
        result, _ = self.run_case("unfinished_tool")
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["retryable"])

    def test_timeout_interrupts_and_collects_terminal(self):
        result, _ = self.run_case("timeout", timeout=0.2)
        self.assertEqual(result["error_kind"], "timeout")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["quiescent"])

    def test_cancel_uses_interrupt_without_disabling_permissions(self):
        start = time.monotonic()
        result, _ = self.run_case("timeout", cancel=lambda: time.monotonic() - start > 0.2)
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["quiescent"])

    def test_approval_is_denied_and_reported(self):
        result, _ = self.run_case("approval")
        self.assertEqual(result["error_kind"], "permission_required")
        self.assertFalse(result["retryable"])

    def test_missing_terminal_after_interrupt_remains_unknown(self):
        result, _ = self.run_case("hang", timeout=0.1)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["quiescent"])

    def test_malformed_protocol_before_execution_is_bounded(self):
        result, _ = self.run_case("malformed")
        self.assertEqual(result["error_kind"], "transport")
        self.assertTrue(result["quiescent"])

    def test_callback_error_still_reaps_process(self):
        events = []

        def fail(event):
            events.append(event)
            raise RuntimeError("failed state write")

        with self.assertRaises(RuntimeError):
            self.run_case("success", callback=fail)
        self.assertFalse(process_present(events[0]["pid"]))

    def test_incidental_503_text_is_not_classified_as_quota(self):
        self.assertEqual(error_kind({"message": "the code mentions quota and 503"}), "unknown")
        with patch.dict(os.environ, {"TEST_API_KEY": "never-print-this-secret"}):
            self.assertNotIn("never-print-this-secret", safe_message({"message": "key never-print-this-secret"}))


if __name__ == "__main__":
    unittest.main()
