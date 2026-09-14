import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/task-router"
sys.path.insert(0, str(ROOT / "scripts"))
from task_router_runtime.service import public_report, worker_guard
from task_router_runtime.store import StateError


class LauncherTests(unittest.TestCase):
    def test_installed_entry_is_found_without_source_paths_or_remote_catalog(self):
        code = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["task-router"]["args"][-1]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex = home / ".codex"
            codex.mkdir()
            (codex / "config.toml").write_text('[plugins."task-router@team-local"]\nenabled = true\n')
            entry = codex / "plugins/cache/team-local/task-router/0.5.0/scripts/bootstrap_mcp.py"
            entry.parent.mkdir(parents=True)
            entry.write_text("# fixture")
            response = SimpleNamespace(returncode=0, stdout=json.dumps({"installed": [
                {"name": "task-router", "marketplaceName": "team-local", "version": "0.5.0", "enabled": True}]}))
            with patch("pathlib.Path.home", return_value=home), patch("os.environ", {}), \
                    patch("subprocess.run", return_value=response) as call, patch("runpy.run_path") as run:
                exec(compile(code, "plugin-launcher", "exec"), {})
            self.assertIn("--marketplace", call.call_args.args[0])
            self.assertIn("team-local", call.call_args.args[0])
            self.assertEqual(call.call_args.kwargs["stdin"], subprocess.DEVNULL)
            run.assert_called_once_with(str(entry), run_name="__main__")

    def test_launcher_rejects_traversal_in_installation_metadata(self):
        code = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["task-router"]["args"][-1]
        response = SimpleNamespace(returncode=0, stdout=json.dumps({"installed": [
            {"name": "task-router", "marketplaceName": "..", "version": "0.5.0", "enabled": True}]}))
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.home", return_value=Path(directory)), \
                patch("os.environ", {"TASK_ROUTER_MARKETPLACE": "team"}), patch("subprocess.run", return_value=response), \
                patch("runpy.run_path") as run, self.assertRaises(RuntimeError):
            exec(compile(code, "plugin-launcher", "exec"), {})
        run.assert_not_called()

    def test_existing_sdk_uses_current_interpreter(self):
        spec = importlib.util.spec_from_file_location("bootstrap_test", PLUGIN / "scripts/bootstrap_mcp.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, "installed_sdk", return_value=True), patch.object(module.venv, "EnvBuilder") as builder:
            self.assertEqual(module.interpreter(), Path(sys.executable))
        builder.assert_not_called()

    def test_worker_cannot_access_broker(self):
        with patch.dict(os.environ, {"TASK_ROUTER_WORKER": "1"}), self.assertRaises(StateError):
            worker_guard()

    def test_long_results_can_be_read_without_losing_content(self):
        text = "x" * 29000
        report = {"task_id": "a", "status": "succeeded", "result": {"result_text": text}, "attempts": []}
        first = public_report(report)
        second = public_report(report, first["next_result_offset"])
        third = public_report(report, second["next_result_offset"])
        self.assertEqual(first["result_text"] + second["result_text"] + third["result_text"], text)
        self.assertIsNone(third["next_result_offset"])


if __name__ == "__main__":
    unittest.main()
