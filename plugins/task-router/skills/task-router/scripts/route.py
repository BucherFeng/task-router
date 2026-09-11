#!/usr/bin/env python3
"""Resolve a task type to its routing decision from routing.json."""

import argparse
import json
import sys
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "routing.json"


def load_config(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"error: routing config not found at {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"error: invalid JSON in {path}: {exc}")


def resolve(cfg: dict, task: str):
    routes = cfg.get("routes", {})
    route = routes.get(task)
    fallback = False
    if route is None:
        route = cfg.get("fallback", {"delegate": False})
        fallback = True

    delegate = bool(route.get("delegate", False))
    model = route.get("model")

    if model is not None:
        if model == "primary":
            model = cfg.get("defaults", {}).get("primary")
        if model not in cfg.get("models", {}):
            sys.exit(f"error: route '{task}' points at model '{model}', "
                     f"which is not present in the models catalog")

    return {"task": task, "delegate": delegate, "model": model, "fallback": fallback}


def failover_chain(cfg: dict, model: str):
    catalog = cfg.get("models", {})
    if model not in catalog:
        sys.exit(f"error: model '{model}' is not present in the models catalog")
    chain = cfg.get("failover", {}).get("chains", {}).get(model, [])
    return {"model": model, "chain": [m for m in chain if m in catalog]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", nargs="?", default=None,
                        help="task type, e.g. edit, long-context, discussion")
    parser.add_argument("--task", dest="task_option", default=None,
                        help="task type as a flag (same as the positional argument)")
    parser.add_argument("--failover", dest="failover_model", default=None, metavar="MODEL",
                        help="print the failover chain for MODEL and exit")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="path to routing.json (default: alongside this script)")
    parser.add_argument("--list", action="store_true",
                        help="list all routes and exit")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.failover_model is not None:
        print(json.dumps(failover_chain(cfg, args.failover_model), ensure_ascii=False))
        return

    if args.list:
        defaults = cfg.get("defaults", {})
        print(f"primary model: {defaults.get('primary', '(unset)')}")
        for name, route in cfg.get("routes", {}).items():
            target = route.get("model", "(stay on primary)")
            mode = "delegate" if route.get("delegate") else "inline"
            print(f"  {name:<14} -> {mode:<8} {target}")
        return

    task = args.task_option or args.task
    if task is None:
        parser.error("provide a task type (or use --list to show all routes)")

    print(json.dumps(resolve(cfg, task), ensure_ascii=False))


if __name__ == "__main__":
    main()
