import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from test_install import FAKE_CODEX, MARKETPLACE

ROOT = Path(__file__).resolve().parents[1]


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append((self.path, body))
        status = 503 if body["model"].startswith("gpt-") else 200
        data = json.dumps({"model": body["model"], "status": status}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class CompleteInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.config = self.home / ".codex/config.toml"
        self.config.parent.mkdir(parents=True)
        self.binary = self.root / "bin"
        self.binary.mkdir()
        (self.binary / "codex").write_text(FAKE_CODEX)
        (self.binary / "codex").chmod(0o755)
        shutil.copy2(ROOT / "tests/fixtures/fake_systemctl.py", self.binary / "systemctl")
        (self.binary / "systemctl").chmod(0o755)
        self.env = {**os.environ, "PATH": str(self.binary) + os.pathsep + os.environ["PATH"],
                    "TASK_ROUTER_INSTALL_TESTING": "1", "TASK_ROUTER_SYSTEMD_FIXTURE": str(self.root),
                    "FAKE_CODEX_LOG": str(self.root / "codex.jsonl"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.env.pop("TASK_ROUTER_CONFIG", None)
        self.addCleanup(lambda: subprocess.run([str(self.binary / "systemctl"), "--user", "stop", "task-router-proxy"],
                                              env=self.env, capture_output=True))
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.upstream.calls = []
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.addCleanup(self.upstream.server_close)
        self.addCleanup(self.upstream.shutdown)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.upstream_url = f"http://127.0.0.1:{self.upstream.server_port}/account/v1"
        self.original = ('model_provider="fixture"\nmodel="gpt-6-astra"\n[model_providers.fixture]\n'
                         f'base_url="{self.upstream_url}"\nwire_api="responses"\nenv_key="FAKE_KEY"\n')
        self.config.write_text(self.original)

    def install(self):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/install.py"),
                               "--home", str(self.home), "--proxy-port", str(self.port)],
                              env=self.env, capture_output=True, text=True, timeout=35)

    def test_complete_cli_installs_proxy_and_switches_failing_family(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        provider = tomllib.loads(self.config.read_text())["model_providers"]["fixture"]
        self.assertEqual(provider["base_url"], f"http://127.0.0.1:{self.port}/account/v1")
        self.assertEqual(provider["env_key"], "FAKE_KEY")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request = {"model": "gpt-6-astra", "input": [{"role": "user", "content": "keep context"}]}
        conn.request("POST", "/account/v1/responses", json.dumps(request), {"Content-Type": "application/json"})
        response = conn.getresponse()
        payload = json.loads(response.read())
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["model"], "glm-5.3")
        self.assertEqual([call[0] for call in self.upstream.calls], ["/account/v1/responses"] * 2)
        self.assertEqual(self.upstream.calls[1][1]["input"], request["input"])
        self.assertEqual(len(self.upstream.calls), 2)
        routing = self.home / ".config/task-router/routing.json"
        saved_routing, saved_config = routing.read_bytes(), self.config.read_bytes()
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(saved_routing, routing.read_bytes())
        self.assertEqual(saved_config, self.config.read_bytes())

    def test_failed_service_start_keeps_original_connection(self):
        self.env["TASK_ROUTER_SYSTEMD_FAIL"] = "1"
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse((self.home / ".local/state/task-router/original-base-url").exists())
        self.assertFalse((self.home / ".config/task-router/routing.json").exists())
        self.assertNotIn("installed task-router", result.stdout)


if __name__ == "__main__":
    unittest.main()
