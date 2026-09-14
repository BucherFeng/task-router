#!/usr/bin/env python3
"""Standalone task-router prototype: submit, run, inspect, cancel, and resume."""

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROUTER = REPO / "plugins/task-router/skills/task-router/scripts"
sys.path.insert(0, str(ROUTER))
from router_core import ConfigError, catalog_models, choose, config_path, digest, normalize, read_json
from route import DEFAULT_CONFIG, host_settings
from task_router_runtime.controller import Controller
from task_router_runtime.store import StateError, Store


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--state-dir", type=Path, default=Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "task-router/controller")
    sub = cli.add_subparsers(dest="action", required=True)
    for name in ("submit", "run"):
        command = sub.add_parser(name)
        command.add_argument("--task", required=True, help="explicit task type from routing policy")
        command.add_argument("--cwd", required=True, type=Path, help="bounded working directory")
        prompt = command.add_mutually_exclusive_group(required=True)
        prompt.add_argument("--prompt")
        prompt.add_argument("--prompt-file", type=Path)
        command.add_argument("--write", action="store_true", help="authorize local edits inside cwd")
        command.add_argument("--config", type=Path)
        command.add_argument("--request-key", help="same key and input returns the same task")
        command.add_argument("--timeout", type=int, default=300, help="total task wall time after its first attempt")
        command.add_argument("--verify-json", help="explicit verification argv as a JSON string array")
        if name == "run":
            command.add_argument("--one-attempt", action="store_true", help="stop at a persisted retry checkpoint")
    status = sub.add_parser("status")
    status.add_argument("task_id", nargs="?")
    resume = sub.add_parser("resume")
    resume.add_argument("task_id")
    resume.add_argument("--one-attempt", action="store_true")
    cancel = sub.add_parser("cancel")
    cancel.add_argument("task_id")
    return cli


def task_payload(args, state_dir):
    cwd = args.cwd.expanduser().resolve(strict=True)
    if not cwd.is_dir():
        raise StateError("cwd must be a directory")
    state = state_dir.expanduser().resolve()
    if state == cwd or cwd in state.parents:
        raise StateError("state directory must be outside the worker's writable workspace")
    if not 1 <= args.timeout <= 3600:
        raise StateError("timeout must be between 1 and 3600 seconds")
    if args.prompt_file is not None and args.prompt_file.stat().st_size > 400000:
        raise StateError("prompt file is too large")
    prompt = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding="utf-8")
    if not prompt.strip() or len(prompt) > 100000:
        raise StateError("prompt must contain 1..100000 characters")
    path, source = config_path(args.config, DEFAULT_CONFIG)
    policy, notes = normalize(read_json(path))
    if args.task not in policy["tasks"] or "role" not in policy["tasks"][args.task]:
        raise StateError("runner requires a configured task with a model role; inline or unknown tasks are not executed")
    if args.task in {"discussion", "planning", "deep-analysis", "review", "read-code"} and args.write:
        raise StateError("discussion/review/read tasks must be read-only")
    if args.task in {"edit", "small-edit", "bulk-rewrite"} and not args.write:
        raise StateError("editing tasks require explicit --write")
    command = json.loads(args.verify_json) if args.verify_json else None
    if command is not None and (type(command) is not list or not command or any(not isinstance(s, str) or not s for s in command)):
        raise StateError("verify-json must be a nonempty argv array")
    settings, host_path = host_settings()
    catalog = None
    if settings.get("model_catalog_json"):
        catalog_path = Path(settings["model_catalog_json"]).expanduser()
        if not catalog_path.is_absolute() and host_path:
            catalog_path = host_path.parent / catalog_path
        catalog = catalog_models(read_json(catalog_path))
    return {"task": args.task, "cwd": str(cwd), "prompt": prompt, "writable": args.write,
            "timeout": args.timeout, "verify": command, "policy": policy, "catalog": catalog,
            "provider": settings.get("model_provider"), "config_hash": digest(policy),
            "config_path": str(path.resolve()), "config_source": source, "migration_notes": notes}


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    try:
        payload = task_payload(args, args.state_dir) if args.action in {"run", "submit"} else None
        store = Store(args.state_dir)
        controller = Controller(store, None, choose)
        if args.action == "status":
            emit(controller.report(args.task_id) if args.task_id else {"tasks": store.list_tasks()})
            return 0
        if args.action == "cancel":
            store.cancel(args.task_id)
            emit(controller.report(args.task_id))
            return 0
        if payload is not None:
            task_id, created = store.submit(payload, args.request_key)
            emit({"task_id": task_id, "created": created, "status": store.task(task_id)["status"], "database": str(store.path)})
            if args.action == "submit":
                return 0
        else:
            task_id = args.task_id
        from task_router_runtime.codex_adapter import CodexAdapter
        cancellation = threading.Event()
        previous = {sig: signal.signal(sig, lambda *_: cancellation.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            controller = Controller(store, CodexAdapter(), choose, cancellation.is_set)
            report = controller.execute(task_id, one_attempt=args.one_attempt)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        emit(report)
        return {"succeeded": 0, "retrying": 75, "cancelled": 130, "unknown": 4}.get(report["status"], 3)
    except (StateError, ConfigError, OSError, ValueError) as exc:
        emit({"status": "error", "error": str(exc)})
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
