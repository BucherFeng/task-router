#!/usr/bin/env python3
"""Local JSON-RPC test peer; never calls a model or external application."""

import json
import os
import sys
from pathlib import Path

scenario = os.environ.get("TASK_ROUTER_FAKE_SCENARIO", "success")
cwd = None
model = None


def emit(message):
    print(json.dumps(message), flush=True)


def terminal(status="completed", error=None, text="fixture done"):
    emit({"method": "item/completed", "params": {"threadId": "thread-one", "turnId": "turn-one",
          "item": {"id": "message-one", "type": "agentMessage", "text": text}}})
    emit({"method": "turn/completed", "params": {"threadId": "thread-one", "turn": {
        "id": "turn-one", "status": status, "items": [], "error": error}}})


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    request = message.get("id")
    if method == "initialize":
        if scenario == "malformed":
            print("{broken", flush=True)
            continue
        emit({"id": request, "result": {"userAgent": "fake-app-server"}})
    elif method == "thread/start":
        cwd = Path(message["params"]["cwd"])
        model = message["params"]["model"]
        if os.environ.get("TASK_ROUTER_FAKE_LOG"):
            with open(os.environ["TASK_ROUTER_FAKE_LOG"], "a", encoding="utf-8") as log:
                log.write(json.dumps({"model": model}) + "\n")
        if scenario == "auth":
            emit({"id": request, "error": {"code": -32000, "message": "authentication failed",
                  "data": {"codexErrorInfo": "unauthorized"}}})
            continue
        emit({"id": request, "result": {"thread": {"id": "thread-one"}}})
    elif method == "turn/start":
        sandbox = message["params"]["sandboxPolicy"]
        assert sandbox["networkAccess"] is False
        assert message["params"]["approvalPolicy"] == "on-request"
        if scenario == "lost_ack":
            raise SystemExit(0)
        emit({"id": request, "result": {"turn": {"id": "turn-one", "status": "inProgress", "items": []}}})
        if scenario == "eof":
            raise SystemExit(0)
        if scenario in ("hang", "timeout"):
            continue
        if scenario == "approval":
            emit({"id": 100, "method": "item/commandExecution/requestApproval", "params": {"threadId": "thread-one", "turnId": "turn-one"}})
            continue
        if scenario == "will_retry":
            emit({"method": "error", "params": {"threadId": "thread-one", "turnId": "turn-one",
                  "willRetry": True, "error": {"message": "simulated overload", "codexErrorInfo": "serverOverloaded"}}})
        emit({"method": "item/started", "params": {"threadId": "thread-one", "turnId": "turn-one",
              "item": {"id": "command-one", "type": "commandExecution", "status": "inProgress"}}})
        if scenario != "unfinished_tool":
            emit({"method": "item/completed", "params": {"threadId": "thread-one", "turnId": "turn-one",
                  "item": {"id": "command-one", "type": "commandExecution", "status": "completed"}}})
        if scenario == "partial":
            progress = cwd / "progress.txt"
            if model == "first":
                assert not progress.exists()
                progress.write_text("step one\n")
                terminal("failed", {"message": "simulated HTTP 503", "codexErrorInfo": "serverOverloaded"})
            else:
                assert progress.read_text() == "step one\n"
                assert "progress.txt" in message["params"]["input"][0]["text"]
                progress.write_text(progress.read_text() + "step two\n")
                terminal(text="continued without repeating step one")
        elif scenario == "service_failure":
            terminal("failed", {"message": "simulated HTTP 503", "codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 503}}})
        else:
            terminal()
    elif method == "turn/interrupt":
        emit({"id": request, "result": {}})
        if scenario != "hang":
            terminal("interrupted")
    elif request == 100:
        assert message["result"]["decision"] == "cancel"
        terminal("interrupted")
