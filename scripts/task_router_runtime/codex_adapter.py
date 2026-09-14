"""Bounded JSON-RPC client for an owned Codex app-server stdio process."""

import json
import os
import re
import selectors
import signal
import subprocess
import time

from .controller import outcome

EFFECT_ITEMS = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall"}


def error_kind(error):
    if not isinstance(error, dict):
        return "unknown"
    info = error.get("codexErrorInfo")
    if info is None and isinstance(error.get("data"), dict):
        info = error["data"].get("codexErrorInfo")
    if info in ("rateLimitExceeded", "usageLimitExceeded", "serverOverloaded", "internalServerError"):
        return "availability"
    if info == "unauthorized":
        return "auth"
    if info == "contextWindowExceeded":
        return "context"
    if info in ("badRequest", "sessionBudgetExceeded", "sandboxError"):
        return "configuration"
    if isinstance(info, dict):
        for value in info.values():
            if isinstance(value, dict):
                status = value.get("httpStatusCode")
                if status in (429, 500, 502, 503, 504):
                    return "availability"
                if status in (401, 403):
                    return "auth"
                if status in (400, 404, 422):
                    return "configuration"
        if any(key in info for key in ("httpConnectionFailed", "responseStreamConnectionFailed", "responseStreamDisconnected", "responseTooManyFailedAttempts")):
            return "transport"
    return "configuration" if error.get("code") in (-32600, -32601, -32602) else "unknown"


def safe_message(error):
    message = str(error.get("message", "Codex execution failed")) if isinstance(error, dict) else "Codex execution failed"
    for key, value in os.environ.items():
        if len(value) >= 8 and any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            message = message.replace(value, "[redacted]")
    message = re.sub(r"(?i)(bearer\s+)[^\s,\"']+", r"\1[redacted]", message)
    message = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[redacted]", message)
    return message[:1000]


def stop_process(process):
    try:
        process.stdin.close()
    except OSError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
    for pipe in (process.stdout, process.stderr):
        pipe.close()
    try:
        os.killpg(process.pid, 0)
        return False
    except ProcessLookupError:
        return True


class CodexAdapter:
    def __init__(self, executable="codex"):
        self.executable = executable

    def run(self, *, prompt, cwd, model, effort, provider, writable, timeout, on_event, cancelled):
        try:
            process = subprocess.Popen([self.executable, "app-server", "--stdio", "-c", "features.multi_agent=false"],
                                       cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
        except OSError as exc:
            return outcome("failed", "configuration", f"Cannot start Codex ({type(exc).__name__}).")
        selector = selectors.DefaultSelector()
        pending = bytearray()
        incoming = bytearray()
        active = set()
        texts = {}
        thread_id = turn_id = None
        turn_sent = False
        terminal = False
        stop_reason = None
        stop_at = None
        interrupt_sent = False
        total_bytes = 0
        result = outcome("unknown", "transport", "Codex disconnected without a terminal outcome.", quiescent=False)
        deadline = time.monotonic() + timeout

        def send(value):
            pending.extend((json.dumps(value) + "\n").encode())
            if len(pending) > 512000:
                raise ValueError("request queue limit exceeded")
            try:
                selector.get_key(process.stdin)
            except KeyError:
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")

        def thread_known(value):
            nonlocal thread_id, turn_sent
            if thread_id is None:
                if not isinstance(value, str) or not value:
                    raise ValueError("missing thread ID")
                thread_id = value
                on_event({"kind": "thread", "thread_id": thread_id})
            if not turn_sent and stop_reason is None:
                sandbox = {"type": "workspaceWrite", "writableRoots": [cwd], "networkAccess": False,
                           "excludeSlashTmp": True, "excludeTmpdirEnvVar": True} if writable else {"type": "readOnly", "networkAccess": False}
                turn_sent = True
                send({"id": 3, "method": "turn/start", "params": {
                    "threadId": thread_id, "model": model, "effort": effort, "cwd": cwd,
                    "approvalPolicy": "on-request", "sandboxPolicy": sandbox,
                    "input": [{"type": "text", "text": prompt}]}})

        def turn_known(value):
            nonlocal turn_id
            if turn_id is None:
                if not isinstance(value, str) or not value:
                    raise ValueError("missing turn ID")
                turn_id = value
                on_event({"kind": "turn", "turn_id": turn_id})

        def item(value, finished=False):
            if not isinstance(value, dict):
                raise ValueError("invalid item")
            kind, item_id = value.get("type"), value.get("id")
            if kind in EFFECT_ITEMS:
                if finished and value.get("status") != "inProgress":
                    active.discard(item_id)
                else:
                    active.add(item_id)
            if finished and kind == "agentMessage":
                texts[item_id] = str(value.get("text", ""))[:128000]

        try:
            on_event({"kind": "process", "pid": process.pid, "pgid": process.pid})
            for pipe, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, label)
            os.set_blocking(process.stdin.fileno(), False)
            send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "task-router-controller", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True}}})
            done = False
            while not done:
                now = time.monotonic()
                if stop_reason is None and (cancelled() or now >= deadline):
                    stop_reason = "cancelled" if cancelled() else "timeout"
                    stop_at = now + 3
                if stop_reason and not turn_sent:
                    terminal = True
                    result = outcome("cancelled" if stop_reason == "cancelled" else "failed", stop_reason, "Stopped before model execution.")
                    break
                if stop_reason and turn_id and not interrupt_sent:
                    interrupt_sent = True
                    send({"id": 4, "method": "turn/interrupt", "params": {"threadId": thread_id, "turnId": turn_id}})
                if stop_at and now >= stop_at:
                    result = outcome("unknown", stop_reason, "No confirmed terminal state after interruption.", quiescent=False)
                    break
                for key, _ in selector.select(timeout=0.1):
                    if key.data == "stdin":
                        try:
                            written = os.write(process.stdin.fileno(), pending)
                            del pending[:written]
                        except BlockingIOError:
                            continue
                        if not pending:
                            selector.unregister(process.stdin)
                        continue
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        if key.data == "stdout":
                            done = True
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > 16 * 1024 * 1024:
                        raise ValueError("server output limit exceeded")
                    if key.data == "stderr":
                        continue  # stderr may contain private provider diagnostics.
                    incoming.extend(chunk)
                    if len(incoming) > 1024 * 1024:
                        raise ValueError("protocol line limit exceeded")
                    while b"\n" in incoming and not done:
                        raw, _, tail = incoming.partition(b"\n")
                        incoming = bytearray(tail)
                        if not raw.strip():
                            continue
                        message = json.loads(raw)
                        if not isinstance(message, dict):
                            raise ValueError("protocol object required")
                        method = message.get("method")
                        params = message.get("params") or {}
                        if method and "id" in message:
                            if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                                send({"id": message["id"], "result": {"decision": "cancel"}})
                            elif method == "item/permissions/requestApproval":
                                send({"id": message["id"], "result": {"permissions": {}, "scope": "turn"}})
                            else:
                                send({"id": message["id"], "error": {"code": -32601, "message": "Interactive operation unavailable in standalone runner"}})
                            stop_reason, stop_at = "permission_required", time.monotonic() + 3
                            continue
                        if not method and "id" in message:
                            request = message["id"]
                            if "error" in message and request in (1, 2, 3):
                                error = message["error"]
                                terminal = request != 3 or turn_id is None
                                kind = error_kind(error)
                                result = outcome("failed" if terminal else "unknown", kind, safe_message(error),
                                                 retryable=terminal and kind in {"availability", "transport"}, quiescent=terminal)
                                done = True
                            elif request == 1:
                                send({"method": "initialized", "params": {}})
                                args = {"cwd": cwd, "model": model,
                                        "sandbox": "workspace-write" if writable else "read-only",
                                        "approvalPolicy": "on-request", "ephemeral": False}
                                if provider:
                                    args["modelProvider"] = provider
                                send({"id": 2, "method": "thread/start", "params": args})
                            elif request == 2:
                                thread_known(message["result"]["thread"]["id"])
                            elif request == 3:
                                turn_known(message["result"]["turn"]["id"])
                            continue
                        if method == "thread/started":
                            thread_known(params["thread"]["id"])
                            continue
                        if params.get("threadId") not in (None, thread_id):
                            continue
                        if method == "turn/started":
                            turn_known(params["turn"]["id"])
                        elif method == "item/started":
                            item(params["item"])
                        elif method == "item/completed":
                            item(params["item"], True)
                        elif method == "error":
                            on_event({"kind": "server_retry" if params.get("willRetry") else "server_error", "status": error_kind(params.get("error"))})
                        elif method == "turn/completed":
                            turn = params["turn"]
                            turn_known(turn["id"])
                            if turn["id"] != turn_id:
                                continue
                            for entry in turn.get("items", []):
                                item(entry, True)
                            terminal = True
                            if stop_reason:
                                result = outcome("cancelled" if stop_reason == "cancelled" else "failed", stop_reason, "Execution interrupted.")
                            elif turn["status"] == "completed":
                                result = outcome("succeeded", text=next(reversed(texts.values()), ""))
                            elif turn["status"] == "failed":
                                kind = error_kind(turn.get("error"))
                                result = outcome("failed", kind, safe_message(turn.get("error")), retryable=kind in {"availability", "transport"})
                            else:
                                result = outcome("failed", "cancelled", "Turn was interrupted.")
                            done = True
                if process.poll() is not None and not selector.get_map():
                    break
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result = outcome("unknown" if turn_sent else "failed", "transport",
                             f"Protocol interrupted ({type(exc).__name__}).", retryable=not turn_sent, quiescent=not turn_sent)
        finally:
            selector.close()
            stopped = stop_process(process)
        result.update(thread_id=thread_id, turn_id=turn_id)
        result["quiescent"] = bool(stopped and not active and (terminal or not turn_sent))
        if not result["quiescent"]:
            result.update(status="unknown", retryable=False)
        return result
