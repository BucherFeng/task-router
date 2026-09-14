import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/task-router/skills/task-router/scripts"
sys.path.insert(0, str(SCRIPTS))
from router_core import (ConfigError, catalog_models, choose, config_path, migrate,
                         normalize, read_json, runtime_models, validate, write_new)


def policy():
    return {"schema_version": 2, "dispatcher": {"model_id": "host"},
            "profiles": {"main": {"model_id": "first", "enabled": True, "reasoning_effort": "high"},
                         "backup": {"model_id": "second", "enabled": True, "reasoning_effort": "low"}},
            "roles": {"coding": ["main", "backup"]},
            "tasks": {"edit": {"role": "coding"}, "trivial": {"inline": True}},
            "fallback": {"inline": True}, "max_attempts": 3}


def legacy():
    return {"version": 1, "defaults": {"primary": "host"},
            "models": {"host": {}, "first": {}, "second": {}},
            "routes": {"edit": {"delegate": True, "model": "first"}, "small-edit": {"delegate": False}},
            "fallback": {"delegate": False},
            "failover": {"on_status": [429, 503], "cooldown_seconds": 300,
                         "chains": {"first": ["second"], "second": ["first"]}}}


class ConfigurationTests(unittest.TestCase):
    def test_rejects_invalid_v2_before_selecting(self):
        mutations = [
            lambda c: c["profiles"]["main"].update(enabled="false"),
            lambda c: c["profiles"]["main"].pop("model_id"),
            lambda c: c["profiles"]["main"].update(model_id=None),
            lambda c: c["roles"].update(coding=[]),
            lambda c: c["roles"].update(coding=["main", "main"]),
            lambda c: c["roles"].update(coding=["unknown"]),
            lambda c: c["tasks"].update(edit={"role": "missing"}),
            lambda c: c["tasks"].update(edit={"inline": False}),
            lambda c: c["tasks"].update(edit={"inline": True, "role": "coding"}),
            lambda c: c.update(schema_version=True),
            lambda c: c.update(max_attempts=True),
            lambda c: c.update(max_attempts=100),
            lambda c: c.update(cooldown_seconds=300),
        ]
        for mutation in mutations:
            config = policy()
            mutation(config)
            with self.subTest(config=config), self.assertRaises(ConfigError):
                validate(config)
        with self.assertRaises(ConfigError):
            normalize([])

    def test_legacy_bad_configuration_is_not_silently_repaired(self):
        for route in ({"delegate": "false"}, {"delegate": True}, {"delegate": True, "model": "typo"}):
            config = legacy()
            config["routes"]["edit"] = route
            with self.subTest(route=route), self.assertRaises(ConfigError):
                migrate(config)
        for chain in (["first"], ["second", "second"], ["missing"]):
            config = legacy()
            config["failover"]["chains"]["first"] = chain
            with self.subTest(chain=chain), self.assertRaises(ConfigError):
                migrate(config)

    def test_migration_preserves_policy_and_flattens_cycles(self):
        config = migrate(legacy())
        self.assertEqual(config["roles"]["legacy-edit"], ["first", "second"])
        self.assertEqual(config["tasks"]["small-edit"], {"inline": True})
        self.assertNotIn("cooldown_seconds", json.dumps(config))
        self.assertEqual(normalize(config)[0], config)

    def test_duplicate_json_keys_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"profiles": {}, "profiles": {}}')
            with self.assertRaises(ConfigError):
                read_json(path)

    def test_creation_never_overwrites_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            write_new(path, policy())
            previous = path.read_bytes()
            with self.assertRaises(ConfigError):
                write_new(path, {})
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class SelectionTests(unittest.TestCase):
    def test_selects_next_model_with_independent_effort(self):
        config = policy()
        catalog = {"first": {"efforts": ["low"]}, "second": {"efforts": ["low"]}}
        result = choose(config, "edit", catalog=catalog)
        self.assertEqual((result["model"], result["reasoning_effort"]), ("second", "low"))
        self.assertIn("reasoning_effort_unsupported", result["candidates"][0]["reasons"])

    def test_profile_change_updates_all_tasks_without_rewriting_routes(self):
        config = policy()
        config["tasks"]["test"] = {"role": "coding"}
        config["profiles"]["main"]["model_id"] = "new-model"
        for task in ("edit", "test"):
            self.assertEqual(choose(config, task)["model"], "new-model")

    def test_runtime_deny_and_unverified_are_distinct(self):
        runtime = {"complete": False, "models": {"first": {"delegation": False}}}
        result = choose(policy(), "edit", runtime=runtime)
        self.assertEqual(result["model"], "second")
        self.assertTrue(result["warnings"])
        self.assertEqual(choose(policy(), "edit", runtime=runtime, require_verified=True)["status"], "unavailable")
        runtime["models"]["second"] = {"delegation": True, "reasoning_efforts": ["low"]}
        self.assertEqual(choose(policy(), "edit", runtime=runtime, require_verified=True)["model"], "second")

    def test_limits_context_using_input_and_output_and_all_sources(self):
        config = policy()
        config["profiles"]["main"]["context_window"] = 100
        config["profiles"]["backup"]["context_window"] = 300
        catalog = {"first": {"context_window": 1000}, "second": {"context_window": 500}}
        result = choose(config, "edit", catalog=catalog, input_tokens=90, output_tokens=20)
        self.assertEqual(result["model"], "second")
        self.assertEqual(choose(policy(), "edit", input_tokens=1)["status"], "unavailable")

    def test_required_tools_cannot_be_assumed(self):
        config = policy()
        self.assertEqual(choose(config, "edit", required=["tools"])["status"], "unavailable")
        runtime = {"complete": False, "models": {"second": {"delegation": True, "capabilities": ["text", "tools"]}}}
        self.assertEqual(choose(config, "edit", required=["tools"], runtime=runtime)["model"], "second")

    def test_worker_cannot_redispatch_and_unknown_is_inline(self):
        self.assertFalse(choose(policy(), "edit", execution_role="worker")["delegate"])
        result = choose(policy(), "unknown")
        self.assertTrue(result["fallback"])
        self.assertFalse(result["delegate"])

    def test_disabled_excluded_and_budget_cannot_fall_back_to_host(self):
        config = policy()
        config["profiles"]["main"]["enabled"] = False
        result = choose(config, "edit", excludes=["second"])
        self.assertEqual((result["status"], result["model"]), ("unavailable", None))
        self.assertEqual(choose(policy(), "edit", attempt=4)["reason"], "attempt_budget_exhausted")

    def test_cross_provider_requires_matching_host(self):
        config = policy()
        config["profiles"]["main"]["provider"] = "other"
        self.assertEqual(choose(config, "edit", provider="current")["model"], "second")

    def test_same_model_profiles_are_not_repeated_fallbacks(self):
        config = policy()
        config["profiles"]["backup"]["model_id"] = "first"
        result = choose(config, "edit")
        self.assertEqual(sum(c["eligible"] for c in result["candidates"]), 1)

    def test_runtime_snapshot_expiry(self):
        now = datetime.now(timezone.utc)
        raw = {"schema_version": 1, "observed_at": now.isoformat(), "source": "test",
               "complete": False, "models": {"first": {"delegation": True}}}
        self.assertEqual(runtime_models(raw, now), raw)
        raw["observed_at"] = (now - timedelta(hours=2)).isoformat()
        with self.assertRaises(ConfigError):
            runtime_models(raw, now)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "source.json"
        self.config.write_text(json.dumps(policy()))
        self.host = self.root / "codex.toml"
        self.host.write_text('model = "host"\n')
        self.env = {**os.environ, "XDG_CONFIG_HOME": str(self.root / "xdg"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.env.pop("TASK_ROUTER_CONFIG", None)

    def call(self, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPTS / "route.py"), *map(str, args)],
                              capture_output=True, text=True, env=self.env)

    def test_conflicting_modes_rejected(self):
        for args in (("edit", "--task", "trivial"), ("--list", "--validate"), ("--failover", "first")):
            with self.subTest(args=args):
                self.assertEqual(self.call(*args).returncode, 2)

    def test_bad_explicit_config_does_not_fall_back(self):
        result = self.call("--validate", "--config", self.root / "absent")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "error")

    def test_user_env_explicit_precedence(self):
        user = self.root / "xdg/task-router/routing.json"
        user.parent.mkdir(parents=True)
        user.write_text(json.dumps(policy()))
        self.assertEqual(json.loads(self.call("--validate").stdout)["config_source"], "user")
        self.env["TASK_ROUTER_CONFIG"] = str(self.config)
        self.assertEqual(json.loads(self.call("--validate").stdout)["config_source"], "environment")
        self.assertEqual(json.loads(self.call("--validate", "--config", user).stdout)["config_source"], "argument")

    def test_no_candidate_is_nonzero_with_explanation(self):
        result = self.call("--task", "edit", "--config", self.config, "--codex-config", self.host,
                           "--exclude-model", "first", "--exclude-model", "second", "--explain")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"], "no_compatible_candidate")

    def test_failover_uses_task_pool_and_previous_exclusions(self):
        result = self.call("--failover", "first", "--for-task", "edit", "--config", self.config,
                           "--codex-config", self.host, "--attempt", "2")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["chain"], ["second"])

    def test_migration_writes_new_file_and_preserves_source(self):
        self.config.write_text(json.dumps(legacy()))
        before = self.config.read_bytes()
        output = self.root / "migrated.json"
        result = self.call("--migrate", "--config", self.config, "--output", output)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(read_json(output)["schema_version"], 2)
        self.assertEqual(self.call("--migrate", "--config", self.config, "--output", output).returncode, 2)

    def test_doctor_does_not_claim_live_connectivity(self):
        result = self.call("--doctor", "--config", self.config, "--codex-config", self.host)
        output = json.loads(result.stdout)
        self.assertFalse(output["live_api_tested"])
        self.assertFalse(output["persistent_cooldown_implemented"])
        self.assertEqual(output["status"], "attention")

    def test_explicit_relative_catalog_is_relative_to_working_directory(self):
        catalog = self.root / "catalog.json"
        catalog.write_text(json.dumps({"models": [{"slug": "first", "supported_reasoning_levels": [{"effort": "high"}]}]}))
        relative = os.path.relpath(catalog, Path.cwd())
        result = self.call("--task", "edit", "--config", self.config, "--codex-config", self.host,
                           "--catalog", relative)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["model"], "first")

    def test_doctor_with_partial_evidence_still_flags_unverified_selection(self):
        runtime = self.root / "runtime.json"
        runtime.write_text(json.dumps({"schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
                                       "source": "test", "complete": False, "models": {}}))
        catalog = self.root / "models.json"
        catalog.write_text(json.dumps({"models": [{"slug": "first", "supported_reasoning_levels": [{"effort": "high"}]}]}))
        result = self.call("--doctor", "--config", self.config, "--codex-config", self.host,
                           "--runtime", runtime, "--catalog", catalog)
        self.assertEqual(json.loads(result.stdout)["status"], "attention")


if __name__ == "__main__":
    unittest.main()
