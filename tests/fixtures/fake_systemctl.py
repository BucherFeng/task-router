#!/usr/bin/env python3
"""Test-only user service manager. Launches only the copied loopback model proxy."""

import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["TASK_ROUTER_SYSTEMD_FIXTURE"])
home = root / "home"
state_file = root / "fake-systemd.json"
state = json.loads(state_file.read_text()) if state_file.exists() else {}
args = sys.argv[1:]
assert args[0] == "--user"
command = args[1]


def alive():
    if not state.get("pid"):
        return False
    try:
        os.kill(state["pid"], 0)
        return True
    except ProcessLookupError:
        return False


def stop():
    if alive():
        try:
            os.killpg(state["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    state.pop("pid", None)


if command == "is-active":
    raise SystemExit(0 if alive() else 3)
elif command == "is-enabled":
    raise SystemExit(0 if state.get("enabled") else 1)
elif command == "enable":
    state["enabled"] = True
elif command == "disable":
    state["enabled"] = False
elif command == "stop":
    stop()
elif command == "restart":
    stop()
    if os.environ.get("TASK_ROUTER_SYSTEMD_FAIL") == "1":
        raise SystemExit(17)
    unit = home / ".config/systemd/user/task-router-proxy.service"
    line = next(line for line in unit.read_text().splitlines() if line.startswith("ExecStart="))
    argv = [word.replace("%%", "%").replace("$$", "$") for word in shlex.split(line.removeprefix("ExecStart="))]
    assert Path(argv[2]).resolve() == (home / ".local/share/task-router/model_proxy.py").resolve()
    assert argv[argv.index("--listen") + 1].startswith("127.0.0.1:")
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    state["pid"] = process.pid
elif command not in ("daemon-reload", "show-environment"):
    raise SystemExit(2)
state_file.write_text(json.dumps(state))
