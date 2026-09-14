#!/usr/bin/env python3
"""Start the plugin MCP, preparing a private SDK environment only when needed."""

import importlib.metadata
import json
import os
import subprocess
import sys
import venv
from pathlib import Path

SDK = "mcp==1.27.0"


def installed_sdk():
    try:
        version = importlib.metadata.version("mcp").split(".")
        return int(version[0]) == 1 and int(version[1]) >= 27
    except (importlib.metadata.PackageNotFoundError, ValueError, IndexError):
        return False


def interpreter():
    if installed_sdk():
        return Path(sys.executable)
    import fcntl
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    if not data.is_absolute():
        raise RuntimeError("XDG_DATA_HOME must be absolute")
    root = data / "task-router" / f"mcp-1.27.0-py{sys.version_info.major}.{sys.version_info.minor}"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise RuntimeError("SDK cache must not be a symlink")
    descriptor = os.open(root / "setup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        python = root / "venv/bin/python"
        if python.is_file():
            check = subprocess.run([str(python), "-c", "from mcp.server.fastmcp import FastMCP"], capture_output=True, timeout=20)
            if check.returncode == 0:
                return python
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        result = subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", SDK],
                                capture_output=True, timeout=150)
        if result.returncode:
            raise RuntimeError("Automatic MCP dependency setup failed; plugin diagnostics need attention.")
        return python
    finally:
        os.close(descriptor)


def main():
    if sys.version_info < (3, 11):
        raise RuntimeError("Task Router requires Python 3.11 or newer")
    python = interpreter()
    script = Path(__file__).resolve().with_name("mcp_server.py")
    if "--check" in sys.argv:
        print(json.dumps({"ready": True, "python": str(python), "server": str(script)}))
        return
    os.execv(str(python), [str(python), "-B", str(script)])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Task Router startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
