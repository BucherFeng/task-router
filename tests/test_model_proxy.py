import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from model_proxy import CooldownState, build_server


class FakeUpstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.handle_any()

    def do_POST(self):
        self.handle_any()

    def handle_any(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        try:
            model = json.loads(body)["model"] if body else None
        except (ValueError, KeyError, TypeError):
            model = None
        self.server.requests.append(model)
        status, payload, content_type = self.server.logic(model)
        if status in (429, 502, 503):
            body = json.dumps({"error": {"message": payload}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if content_type == "text/event-stream":
            self.send_header("Connection", "close")
            self.end_headers()
            for part in payload:
                self.wfile.write(part.encode() if isinstance(part, str) else part)
                self.wfile.flush()
                time.sleep(0.02)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            body = json.dumps({"ok": True, "model_seen": model}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.upstream_requests = []
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
        self.upstream.requests = self.upstream_requests
        self.upstream.logic = self.upstream_logic
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.upstream_port = self.upstream.server_address[1]
        self.addCleanup(self.upstream.server_close)

    def upstream_logic(self, model):
        raise NotImplementedError

    def start_proxy(self, cooldown=600, state_file=None, fail_statuses="429,502,503",
                    glm_fallback="gpt-6-astra", gpt_fallback="glm-5.3"):
        state = state_file or (self.root / "proxy-state.json")
        server = build_server("127.0.0.1:0", f"http://127.0.0.1:{self.upstream_port}",
                              glm_fallback, gpt_fallback, state, cooldown, fail_statuses.split(","))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1], state

    def request(self, port, model="glm-5.3", body=None, method="POST"):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        if body is None:
            body = json.dumps({"model": model, "input": "ping"})
        connection.request(method, "/v1/responses", body=body,
                           headers={"Authorization": "Bearer test", "Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read()
        return response.status, data, dict(response.getheaders())

    def test_503_switches_family_and_cooldowns_it(self):
        self.upstream.logic = lambda model: (
            (503, "glm overloaded", "application/json")
            if model.startswith("glm") else (200, [b"fixture done"], "text/event-stream"))
        port, _ = self.start_proxy()
        status, data, headers = self.request(port, model="glm-5.3")
        self.assertEqual(status, 200)
        self.assertIn(b"fixture done", data)
        self.assertEqual(self.upstream_requests, ["glm-5.3", "gpt-6-astra"])
        self.assertEqual(headers.get("X-Task-Router-Failover"), "glm exhausted -> gpt-6-astra")

    def test_cooldown_skips_dead_family_without_upstream_retry(self):
        self.upstream.logic = lambda model: (
            (503, "glm overloaded", "application/json")
            if model.startswith("glm") else (200, "stream", "text/event-stream"))
        port, _ = self.start_proxy()
        self.request(port, model="glm-5.3")
        self.request(port, model="glm-5.3")
        self.assertEqual(self.upstream_requests, ["glm-5.3", "gpt-6-astra", "gpt-6-astra"])

    def test_gpt_503_switches_to_glm(self):
        self.upstream.logic = lambda model: (
            (503, "gpt overloaded", "application/json")
            if model.startswith("gpt") else (200, "stream", "text/event-stream"))
        port, _ = self.start_proxy()
        status, _, _ = self.request(port, model="gpt-6-astra")
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream_requests, ["gpt-6-astra", "glm-5.3"])

    def test_successful_retry_does_not_cool_the_backup_family(self):
        self.upstream.logic = lambda model: (
            (503, "glm overloaded", "application/json")
            if model.startswith("glm") else (200, [b"ok"], "text/event-stream"))
        port, state_file = self.start_proxy()
        self.request(port, model="glm-5.3")
        state = json.loads(Path(state_file).read_text())
        self.assertNotIn("gpt", state["families"])

    def test_both_families_failed_returns_second_error_verbatim(self):
        self.upstream.logic = lambda model: (503, "everything down", "application/json")
        port, state_file = self.start_proxy()
        status, data, _ = self.request(port, model="glm-5.3")
        self.assertEqual(status, 503)
        self.assertIn(b"everything down", data)
        state = json.loads(Path(state_file).read_text())
        self.assertIn("glm", state["families"])
        self.assertIn("gpt", state["families"])

    def test_streaming_response_is_passed_through_in_order(self):
        self.upstream.logic = lambda model: (
            200, [b'data: {"delta": 1}\n\n', b'data: {"delta": 2}\n\n', b'data: {"delta": 3}\n\n'],
            "text/event-stream")
        port, _ = self.start_proxy()
        status, data, headers = self.request(port, model="glm-5.3")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "text/event-stream")
        self.assertEqual(data, b"".join([b'data: {"delta": 1}\n\n', b'data: {"delta": 2}\n\n',
                                         b'data: {"delta": 3}\n\n', b"data: [DONE]\n\n"]))

    def test_non_json_body_passes_through_unchanged(self):
        self.upstream.logic = lambda model: (200, "plain", "text/plain")
        port, _ = self.start_proxy()
        status, data, _ = self.request(port, body=b"not json at all")
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream_requests, [None])

    def test_unknown_family_is_never_rewritten(self):
        self.upstream.logic = lambda model: (503, "down", "application/json")
        port, _ = self.start_proxy()
        status, _, _ = self.request(port, model="claude-4")
        self.assertEqual(status, 503)
        self.assertEqual(self.upstream_requests, ["claude-4"])

    def test_cooldown_expires_after_duration(self):
        self.upstream.logic = lambda model: (
            (503, "glm overloaded", "application/json")
            if model.startswith("glm") else (200, "stream", "text/event-stream"))
        port, _ = self.start_proxy(cooldown=1)
        self.request(port, model="glm-5.3")
        time.sleep(1.1)
        self.request(port, model="glm-5.3")
        self.assertEqual(self.upstream_requests,
                         ["glm-5.3", "gpt-6-astra", "glm-5.3", "gpt-6-astra"])

    def test_cooldown_state_survives_proxy_restart(self):
        self.upstream.logic = lambda model: (
            (503, "glm overloaded", "application/json")
            if model.startswith("glm") else (200, "stream", "text/event-stream"))
        state_file = self.root / "proxy-state.json"
        port, _ = self.start_proxy(state_file=state_file)
        self.request(port, model="glm-5.3")
        port2, _ = self.start_proxy(state_file=state_file)
        self.request(port2, model="glm-5.3")
        self.assertEqual(self.upstream_requests, ["glm-5.3", "gpt-6-astra", "gpt-6-astra"])

    def test_health_endpoint(self):
        port, _ = self.start_proxy()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/health")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b'"status": "ok"', response.read())


class CooldownStateTests(unittest.TestCase):
    def test_state_persists_across_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = CooldownState(path, 600)
            state.cool_family("glm")
            reloaded = CooldownState(path, 600)
            self.assertGreater(reloaded.family_until("glm"), time.time())

    def test_missing_state_file_starts_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            state = CooldownState(Path(directory) / "absent.json", 600)
            self.assertEqual(state.family_until("glm"), 0)


if __name__ == "__main__":
    unittest.main()
