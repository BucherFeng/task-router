import io
import json
import socket
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from proxy_install import ProxyDeployment, ProxyInstallError, set_provider_url, service_quote


CONFIG = '''# User settings stay intact
model_provider = "test.provider"
model = "glm-5.3"
[model_providers."test.provider"]
base_url = 'https://provider.example/custom/v1' # existing endpoint
env_key = "EXAMPLE_API_KEY"
wire_api = "responses"
supports_websockets = true
[projects."/another/project"]
trust_level = "trusted"
'''


class ProxyInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "user space"
        (self.home / ".codex").mkdir(parents=True)
        self.config = self.home / ".codex/config.toml"
        self.config.write_text(CONFIG)
        self.backups = self.home / "backups"
        self.calls = []
        self.active = self.enabled = False
        self.failure = None
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.patcher = patch("proxy_install.run_service", side_effect=self.command)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def command(self, executable, *args, **kwargs):
        self.calls.append(args)
        if args[0] == "is-active":
            return self.active
        if args[0] == "is-enabled":
            return self.enabled
        if self.failure and args[0] == self.failure:
            raise ProxyInstallError("simulated systemctl failure")
        return True

    def prepare(self, **kwargs):
        kwargs.setdefault("port", self.port)
        return ProxyDeployment(home=self.home, repo=ROOT, systemctl="fixture-systemctl", **kwargs)

    def health(self, plan):
        opener = unittest.mock.Mock()
        opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(json.dumps({
            "status": "ok", "service": "task-router-proxy", "instance_id": plan.instance_id}).encode())
        return patch("proxy_install.urllib.request.build_opener", return_value=opener)

    def test_full_install_preserves_path_and_unrelated_provider_settings(self):
        plan = self.prepare()
        self.assertEqual(self.config.read_text(), CONFIG)
        with self.health(plan):
            result = plan.install(self.backups)
        parsed = tomllib.loads(self.config.read_text())
        self.assertEqual(parsed["model_providers"]["test.provider"]["base_url"], f"http://127.0.0.1:{self.port}/custom/v1")
        self.assertFalse(parsed["model_providers"]["test.provider"]["supports_websockets"])
        self.assertEqual(parsed["projects"], tomllib.loads(CONFIG)["projects"])
        self.assertEqual(Path(result["config_backup"]).read_text(), CONFIG)
        service = plan.unit.read_text()
        self.assertIn(service_quote(sys.executable), service)
        self.assertIn(service_quote("https://provider.example"), service)
        self.assertIn('"--fail-status" "503"', service)
        self.assertNotIn("EXAMPLE_API_KEY", service)
        self.assertEqual(plan.marker.read_text(), "https://provider.example/custom/v1")

    def test_repeat_full_install_reuses_original_upstream(self):
        first = self.prepare()
        with self.health(first):
            first.install(self.backups)
        self.active = self.enabled = True
        previous = self.config.read_bytes()
        second = self.prepare()
        self.assertEqual(second.upstream, "https://provider.example/custom/v1")
        with self.health(second):
            second.install(self.backups)
        self.assertEqual(self.config.read_bytes(), previous)

    def test_service_failure_restores_files_and_keeps_codex_direct(self):
        plan = self.prepare()
        self.failure = "restart"
        with self.assertRaises(ProxyInstallError):
            plan.install(self.backups)
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertFalse(plan.marker.exists())
        self.assertFalse(plan.unit.exists())
        self.assertFalse(plan.script.exists())
        self.assertIn(("stop", "task-router-proxy"), self.calls)

    def test_health_from_unrelated_server_does_not_trigger_config_switch(self):
        plan = self.prepare()
        opener = unittest.mock.Mock()
        opener.open.side_effect = lambda *a, **k: io.BytesIO(b'{"status":"ok"}')
        with patch("proxy_install.urllib.request.build_opener", return_value=opener), \
             patch("proxy_install.time.monotonic", side_effect=[0, 16]), self.assertRaises(ProxyInstallError):
            plan.install(self.backups)
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertFalse(plan.unit.exists())

    def test_failed_upgrade_restores_prior_service_and_script(self):
        plan = self.prepare()
        plan.unit.parent.mkdir(parents=True)
        plan.unit.write_text("old unit")
        plan.script.parent.mkdir(parents=True)
        plan.script.write_text("old runtime")
        self.active = self.enabled = True
        plan = self.prepare()
        self.failure = "restart"
        with self.assertRaises(ProxyInstallError):
            plan.install(self.backups)
        self.assertEqual(plan.unit.read_text(), "old unit")
        self.assertEqual(plan.script.read_text(), "old runtime")

    def test_missing_provider_is_preflight_failure(self):
        self.config.write_text('model = "glm-5.3"\n')
        with self.assertRaises(ProxyInstallError):
            self.prepare()
        self.assertFalse((self.home / ".local").exists())

    def test_busy_unmanaged_port_fails_before_file_changes(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            with self.assertRaisesRegex(ProxyInstallError, "occupied"):
                self.prepare(port=server.getsockname()[1])
        self.assertEqual(self.config.read_text(), CONFIG)

    def test_existing_manual_proxy_requires_explicit_original_url(self):
        self.config.write_text(CONFIG.replace("https://provider.example/custom/v1", f"http://127.0.0.1:{self.port}/custom/v1"))
        with self.assertRaisesRegex(ProxyInstallError, "original API"):
            self.prepare()
        plan = self.prepare(upstream_override="https://provider.example/custom/v1")
        self.assertEqual(plan.origin, "https://provider.example")

    def test_provider_change_during_install_preserved(self):
        plan = self.prepare()
        changed = CONFIG.replace("provider.example", "changed.example")
        self.config.write_text(changed)
        with self.health(plan), self.assertRaisesRegex(ProxyInstallError, "provider changed"):
            plan.install(self.backups)
        self.assertEqual(self.config.read_text(), changed)

    def test_plugin_registration_does_not_get_overwritten(self):
        plan = self.prepare()
        self.config.write_text(CONFIG + '\n[plugins."task-router@personal"]\nenabled = true\n')
        with self.health(plan):
            plan.install(self.backups)
        self.assertTrue(tomllib.loads(self.config.read_text())["plugins"]["task-router@personal"]["enabled"])

    def test_toml_edit_handles_quotes_comments_and_file_ending(self):
        for text in (CONFIG, 'model_provider="test"\n[model_providers.test]\nbase_url="https://example.org/v1"'):
            provider = tomllib.loads(text)["model_provider"]
            updated = set_provider_url(text, provider, "http://127.0.0.1:8787/v1")
            self.assertFalse(tomllib.loads(updated)["model_providers"][provider]["supports_websockets"])

    def test_service_arguments_escape_interpretation(self):
        self.assertEqual(service_quote('/path with %h/$NAME/"x"'), '"/path with %%h/$$NAME/\\"x\\""')
        with self.assertRaises(ProxyInstallError):
            service_quote("bad\nargument")


if __name__ == "__main__":
    unittest.main()
