#!/usr/bin/env python3
"""Resolve role-based model pools, validate/migrate policy, and diagnose local setup."""

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

from router_core import (ConfigError, catalog_models, choose, config_path, digest,
                         normalize, read_json, require, runtime_models,
                         user_config_path, write_new)

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "routing.json"


def emit(value):
    print(json.dumps(value, ensure_ascii=True))


def host_settings(explicit=None):
    codex_root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    path = Path(explicit).expanduser() if explicit else codex_root / "config.toml"
    if not path.exists() and explicit is None:
        return {}, None
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        # Never include TOML contents; provider files can contain credentials.
        raise ConfigError(f"{path}: cannot read Codex configuration ({type(exc).__name__})") from exc
    settings = {}
    for field in ("model", "model_provider", "model_catalog_json", "model_reasoning_effort"):
        if field in raw:
            require(isinstance(raw[field], str), f"codex.{field}", "expected a string")
            settings[field] = raw[field]
    return settings, path


def compatibility(args):
    settings, host_path = host_settings(args.codex_config)
    path = args.catalog or settings.get("model_catalog_json")
    catalog = None
    if path:
        path = Path(path).expanduser()
        if not path.is_absolute() and host_path and args.catalog is None:
            path = host_path.parent / path
        catalog = catalog_models(read_json(path))
    snapshot = runtime_models(read_json(args.runtime)) if args.runtime else None
    return settings, catalog, snapshot, str(path) if path else None


def selection(config, task, args, settings, catalog, runtime):
    return choose(config, task, catalog=catalog, runtime=runtime,
                  provider=settings.get("model_provider"), excludes=args.exclude_model,
                  input_tokens=args.input_tokens, output_tokens=args.output_tokens,
                  required=args.require, require_verified=args.require_verified,
                  execution_role=args.execution_role, attempt=args.attempt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", nargs="?", help="task type (legacy positional form)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--task", dest="task_option", help="task type")
    modes.add_argument("--list", action="store_true", help="list configured routes as JSON")
    modes.add_argument("--validate", action="store_true", help="validate the complete policy")
    modes.add_argument("--doctor", action="store_true", help="diagnose configuration and available evidence")
    modes.add_argument("--init-config", action="store_true", help="create a user config from bundled defaults")
    modes.add_argument("--migrate", action="store_true", help="write a migrated config without overwriting files")
    modes.add_argument("--failover", metavar="MODEL", help="select fallback; requires --for-task")
    parser.add_argument("--for-task", help="role context for --failover")
    parser.add_argument("--config", type=Path, help="complete policy; overrides environment and user config")
    parser.add_argument("--output", type=Path, help="new file for --init-config or --migrate")
    parser.add_argument("--catalog", type=Path, help="Codex model catalog (declarations, not live tests)")
    parser.add_argument("--codex-config", type=Path, help="Codex config.toml to inspect")
    parser.add_argument("--runtime", type=Path, help="fresh capability snapshot from this session")
    parser.add_argument("--require-verified", action="store_true", help="reject unconfirmed delegation targets")
    parser.add_argument("--exclude-model", action="append", default=[], help="skip attempted model; repeatable")
    parser.add_argument("--input-tokens", type=int, default=0, help="estimated input including tools and context")
    parser.add_argument("--output-tokens", type=int, default=0, help="reserved output tokens")
    parser.add_argument("--require", action="append", default=[], help="required capability; repeatable")
    parser.add_argument("--attempt", type=int, default=1, help="attempt number supplied by dispatcher")
    parser.add_argument("--execution-role", choices=["dispatcher", "worker"], default="dispatcher")
    parser.add_argument("--explain", action="store_true", help="include every candidate and skip reason")
    args = parser.parse_args()
    active_mode = any((args.task_option is not None, args.list, args.validate, args.doctor,
                       args.init_config, args.migrate, args.failover is not None))
    if args.task is not None and active_mode:
        parser.error("positional task cannot be combined with another action (including --task)")
    if not args.task and not active_mode:
        parser.error("provide a task or an action such as --doctor")
    if args.for_task and not args.failover:
        parser.error("--for-task requires --failover")
    if args.failover and not args.for_task:
        parser.error("--failover requires --for-task TASK because fallback belongs to a role")
    if args.output and not (args.migrate or args.init_config):
        parser.error("--output requires --migrate or --init-config")
    if args.migrate and (args.config is None or args.output is None):
        parser.error("--migrate requires --config SOURCE and --output NEW_FILE")
    if args.init_config and args.config:
        parser.error("--init-config uses bundled defaults; use --migrate for an existing policy")
    if args.input_tokens < 0 or args.output_tokens < 0 or args.attempt < 1:
        parser.error("token counts must be nonnegative and attempt must be positive")
    try:
        if args.init_config:
            config, _ = normalize(read_json(DEFAULT_CONFIG))
            destination = args.output or user_config_path()
            write_new(destination, config)
            emit({"status": "created", "config_path": str(destination), "config_hash": digest(config)})
            return 0
        path, source = config_path(args.config, DEFAULT_CONFIG)
        config, notes = normalize(read_json(path))
        meta = {"config_path": str(path.resolve()), "config_source": source,
                "config_hash": digest(config), "schema_version": 2, "notes": notes}
        if args.migrate:
            write_new(args.output, config)
            emit({**meta, "status": "migrated", "output": str(args.output)})
            return 0
        if args.validate:
            emit({**meta, "status": "valid"})
            return 0
        if args.list:
            emit({**meta, "dispatcher": config["dispatcher"], "tasks": config["tasks"],
                  "roles": config["roles"], "profiles": config["profiles"], "fallback": config["fallback"]})
            return 0
        settings, catalog, runtime, catalog_path = compatibility(args)
        if args.doctor:
            checks = {name: selection(config, name, args, settings, catalog, runtime)
                      for name in config["tasks"]}
            notes = list(notes)
            if settings.get("model") != config["dispatcher"]["model_id"]:
                notes.append("Codex default differs from expected dispatcher; neither proves this thread's actual model.")
            if runtime is None:
                notes.append("No runtime snapshot: delegation unverified. Catalog entries do not prove API availability or quota.")
            if catalog is None:
                notes.append("No catalog found; provide --catalog to check model IDs and declared parameters.")
            emit({**meta, "notes": notes,
                  "status": "attention" if notes or any(r["status"] == "unavailable" or r["warnings"] for r in checks.values()) else "ok",
                  "configured_default_model": settings.get("model"),
                  "expected_dispatcher": config["dispatcher"]["model_id"],
                  "catalog_path": catalog_path, "catalog_count": len(catalog) if catalog is not None else None,
                  "runtime_source": runtime["source"] if runtime else None,
                  "live_api_tested": False, "persistent_cooldown_implemented": False, "routes": checks})
            return 0
        task = args.for_task if args.failover else (args.task_option or args.task)
        if args.failover:
            role = config["tasks"].get(task, config["fallback"]).get("role")
            require(role is not None, "--for-task", "inline tasks have no failover pool")
            pool_models = {config["profiles"][p]["model_id"] for p in config["roles"][role]}
            require(args.failover in pool_models, "--failover", "model does not belong to this task's role")
            args.exclude_model.append(args.failover)
        result = selection(config, task, args, settings, catalog, runtime)
        if args.failover:
            result["failed_model"] = args.failover
            result["chain"] = [c["model"] for c in result["candidates"] if c["eligible"]]
        if not args.explain:
            result.pop("candidates")
        emit({**meta, **result})
        return 3 if result["status"] == "unavailable" else 0
    except ConfigError as exc:
        emit({"status": "error", "error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
