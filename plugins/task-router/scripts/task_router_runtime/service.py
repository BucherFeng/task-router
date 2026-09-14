"""Background submission service used by MCP; jobs survive the frontend process."""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .cli import task_payload
from .controller import Controller
from .store import StateError, Store


def worker_guard():
    if os.environ.get("TASK_ROUTER_WORKER") == "1":
        raise StateError("A managed worker executes its assigned task directly and cannot access the task broker.")


def public_report(report, offset=0):
    if "task_id" not in report:
        return report
    result = report.get("result") or {}
    text = result.get("result_text", "")
    end = offset + 12000
    return {key: report[key] for key in ("task_id", "status", "message", "cwd", "task_type", "cancel_requested") if key in report} | {
        "result_text": text[offset:end], "next_result_offset": end if end < len(text) else None,
        "changed_files": result.get("changed_files", []), "completion_verified": result.get("completion_verified", False),
        "attempts": [{"number": entry["number"], "model": entry["model"], "status": entry["status"],
                      "error_kind": (entry.get("outcome") or {}).get("error_kind")} for entry in report.get("attempts", [])]}


def state_directory():
    value = os.environ.get("TASK_ROUTER_STATE_DIR")
    if value:
        return Path(value).expanduser()
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "task-router/controller"


class Service:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else state_directory()
        self.processes = {}
        self.guard = threading.Lock()

    def status(self, task_id=None):
        worker_guard()
        store = Store(self.directory)
        try:
            return Controller(store, None, None).report(task_id) if task_id else {"tasks": store.list_tasks()}
        finally:
            store.close()

    def _launch(self, task_id):
        with self.guard:
            current = self.processes.get(task_id)
            if current is not None and current.poll() is None:
                return
            process = subprocess.Popen(
                [sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "background_worker.py"),
                 "--state-dir", str(self.directory), task_id], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.processes[task_id] = process

        def reap():
            process.wait()
            with self.guard:
                if self.processes.get(task_id) is process:
                    self.processes.pop(task_id, None)

        threading.Thread(target=reap, daemon=True).start()

    def submit(self, *, task_type, prompt, cwd, allow_write=False, request_key=None, timeout_seconds=300):
        worker_guard()
        if type(allow_write) is not bool or type(timeout_seconds) is not int:
            raise StateError("allow_write must be boolean and timeout_seconds must be integer")
        if not Path(cwd).is_absolute():
            raise StateError("cwd must be an absolute project directory")
        if request_key is None or not request_key.strip() or len(request_key) > 200:
            raise StateError("request_key is required and must be 1..200 characters; reuse it when retrying the same submission")
        args = argparse.Namespace(task=task_type, prompt=prompt, prompt_file=None, cwd=Path(cwd),
                                  write=allow_write, timeout=timeout_seconds, config=None, verify_json=None)
        payload = task_payload(args, self.directory)
        store = Store(self.directory)
        try:
            task_id, created = store.submit(payload, request_key)
            task = store.task(task_id)
            if task["status"] in {"queued", "retrying"}:
                self._launch(task_id)
            return {"task_id": task_id, "created": created, "status": task["status"],
                    "next_action": "Call task_wait to collect progress or the result."}
        finally:
            store.close()

    def resume(self, task_id):
        worker_guard()
        report = self.status(task_id)
        if report["status"] in {"queued", "retrying", "running", "unknown"}:
            self._launch(task_id)
        return self.status(task_id)

    def cancel(self, task_id):
        worker_guard()
        store = Store(self.directory)
        try:
            store.cancel(task_id)
            return Controller(store, None, None).report(task_id)
        finally:
            store.close()

    def wait(self, task_id, wait_seconds=10):
        deadline = time.monotonic() + wait_seconds
        while True:
            report = self.status(task_id)
            if report["status"] in {"succeeded", "failed", "cancelled", "unknown"} or time.monotonic() >= deadline:
                return report
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))

    def diagnose(self):
        worker_guard()
        router = Path(__file__).resolve().parents[2] / "skills/task-router/scripts/route.py"
        result = subprocess.run([sys.executable, "-B", str(router), "--doctor"], capture_output=True, text=True, timeout=20)
        try:
            output = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise StateError("Router diagnostic failed; check plugin installation.") from exc
        return {"entry": "mcp", "controller": "background processes with SQLite state", "routing": output,
                "state_dir": str(self.directory), "model_requests_made": False}
