#!/usr/bin/env python3
"""Detached execution process; keeps running when the front MCP connection ends."""

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

from task_router_runtime.cli import choose
from task_router_runtime.codex_adapter import CodexAdapter
from task_router_runtime.controller import Controller
from task_router_runtime.store import StateError, Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("task_id")
    args = parser.parse_args()
    cancellation = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: cancellation.set())
    store = Store(args.state_dir)
    try:
        # Queue briefly behind the one-execution lock without resetting the task's budget.
        while True:
            task = store.task(args.task_id)
            if cancellation.is_set():
                store.cancel(args.task_id)
            try:
                Controller(store, CodexAdapter(), choose, cancellation.is_set).execute(args.task_id)
                return 0
            except StateError as exc:
                if "another controller is executing" not in str(exc):
                    # An unresolved overlapping workspace remains visible and can be resumed later.
                    store.note(args.task_id, str(exc))
                    return 3
                if time.time() - task["created"] > task["payload"]["timeout"]:
                    store.note(args.task_id, "Queue wait limit reached; task remains saved. Resume after the active task finishes.")
                    return 75
                time.sleep(0.2)
    except BaseException as exc:
        try:
            store.note(args.task_id, f"Background executor stopped ({type(exc).__name__}); inspect task status before resuming.")
        except Exception:
            pass
        return 4
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
