"""Configuration and deterministic selection for task-router (standard library only)."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class ConfigError(ValueError):
    pass


def require(condition, path, message):
    if not condition:
        raise ConfigError(f"{path}: {message}")


def obj(value, path, allowed=None, required=()):
    require(type(value) is dict, path, "expected an object")
    if allowed is not None:
        require(not (value.keys() - set(allowed)), path,
                "unknown fields: " + ", ".join(sorted(value.keys() - set(allowed))))
    require(not (set(required) - value.keys()), path,
            "missing fields: " + ", ".join(sorted(set(required) - value.keys())))
    return value


def string(value, path):
    require(isinstance(value, str) and bool(value.strip()), path, "expected a nonempty string")
    return value


def strings(value, path, nonempty=False):
    require(type(value) is list, path, "expected an array")
    for i, entry in enumerate(value):
        string(entry, f"{path}[{i}]")
    require(len(value) == len(set(value)), path, "duplicate entries")
    require(not nonempty or bool(value), path, "must not be empty")
    return value


def positive(value, path, zero=False):
    require(type(value) is int and value >= (0 if zero else 1), path,
            "expected a nonnegative integer" if zero else "expected a positive integer")


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, str(path), f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        # JSON error messages contain positions, never the configuration contents.
        raise ConfigError(f"{path}: cannot read JSON ({exc})") from exc


def digest(config):
    data = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()[:16]


def user_config_path():
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    require(root.is_absolute(), "XDG_CONFIG_HOME", "expected an absolute path")
    return root / "task-router" / "routing.json"


def config_path(explicit, bundled):
    if explicit is not None:
        return Path(explicit).expanduser(), "argument"
    if "TASK_ROUTER_CONFIG" in os.environ:
        value = string(os.environ["TASK_ROUTER_CONFIG"], "TASK_ROUTER_CONFIG")
        return Path(value).expanduser(), "environment"
    user = user_config_path()
    if user.exists() or user.is_symlink():
        return user, "user"
    return bundled, "bundled"


def validate(config):
    obj(config, "$", {"schema_version", "dispatcher", "profiles", "roles", "tasks", "fallback", "max_attempts"},
        {"schema_version", "dispatcher", "profiles", "roles", "tasks", "fallback", "max_attempts"})
    require(type(config["schema_version"]) is int and config["schema_version"] == 2,
            "schema_version", "expected 2; migrate version 1 with --migrate")
    obj(config["dispatcher"], "dispatcher", {"model_id"}, {"model_id"})
    string(config["dispatcher"]["model_id"], "dispatcher.model_id")
    positive(config["max_attempts"], "max_attempts")
    require(config["max_attempts"] <= 10, "max_attempts", "must be at most 10")
    profiles = obj(config["profiles"], "profiles")
    require(bool(profiles), "profiles", "must not be empty")
    for name, profile in profiles.items():
        string(name, "profiles key")
        path = f"profiles.{name}"
        obj(profile, path, {"model_id", "enabled", "reasoning_effort", "provider", "context_window", "capabilities"},
            {"model_id", "enabled", "reasoning_effort"})
        string(profile["model_id"], path + ".model_id")
        require(type(profile["enabled"]) is bool, path + ".enabled", "expected true or false")
        string(profile["reasoning_effort"], path + ".reasoning_effort")
        if "provider" in profile:
            string(profile["provider"], path + ".provider")
        if "context_window" in profile:
            positive(profile["context_window"], path + ".context_window")
        if "capabilities" in profile:
            strings(profile["capabilities"], path + ".capabilities")
    roles = obj(config["roles"], "roles")
    require(bool(roles), "roles", "must not be empty")
    for name, pool in roles.items():
        string(name, "roles key")
        strings(pool, f"roles.{name}", nonempty=True)
        for profile in pool:
            require(profile in profiles, f"roles.{name}", f"unknown profile: {profile}")

    def task(value, path):
        obj(value, path, {"inline", "role"})
        require(set(value) in ({"inline"}, {"role"}), path, "use exactly one of inline or role")
        if "inline" in value:
            require(value["inline"] is True, path + ".inline", "must be true; use role to delegate")
        else:
            string(value["role"], path + ".role")
            require(value["role"] in roles, path + ".role", "unknown role")

    for name, value in obj(config["tasks"], "tasks").items():
        string(name, "tasks key")
        task(value, f"tasks.{name}")
    task(config["fallback"], "fallback")
    return config


def migrate(config):
    """Preserve v1 routing choices; never infer capabilities from advisory labels."""
    obj(config, "$", {"version", "defaults", "models", "routes", "fallback", "failover"},
        {"version", "defaults", "models", "routes", "fallback"})
    require(type(config["version"]) is int and config["version"] == 1, "version", "expected 1")
    obj(config["defaults"], "defaults", {"primary"}, {"primary"})
    primary = string(config["defaults"]["primary"], "defaults.primary")
    models = obj(config["models"], "models")
    for name, info in models.items():
        string(name, "models key")
        obj(info, f"models.{name}", {"family", "tier", "strengths", "context_window"})
        for key in ("family", "tier", "context_window"):
            if key in info:
                string(info[key], f"models.{name}.{key}")
        if "strengths" in info:
            strings(info["strengths"], f"models.{name}.strengths")
    failure = obj(config.get("failover", {}), "failover", {"chains", "on_status", "cooldown_seconds"})
    if "on_status" in failure:
        require(type(failure["on_status"]) is list, "failover.on_status", "expected an array")
        for code in failure["on_status"]:
            require(type(code) is int and 400 <= code <= 599, "failover.on_status", "invalid HTTP error code")
    if "cooldown_seconds" in failure:
        positive(failure["cooldown_seconds"], "failover.cooldown_seconds", zero=True)
    chains = obj(failure.get("chains", {}), "failover.chains")
    for name, chain in chains.items():
        require(name in models, f"failover.chains.{name}", "unknown source model")
        strings(chain, f"failover.chains.{name}")
        require(name not in chain, f"failover.chains.{name}", "cannot include itself")
        require(all(entry in models for entry in chain), f"failover.chains.{name}", "unknown fallback model")
    result = {"schema_version": 2, "dispatcher": {"model_id": primary},
              "profiles": {name: {"model_id": name, "enabled": True, "reasoning_effort": "default"} for name in models},
              "roles": {}, "tasks": {}, "fallback": {}, "max_attempts": 3}

    def convert(route, name):
        obj(route, name, {"delegate", "model"}, {"delegate"})
        require(type(route["delegate"]) is bool, name + ".delegate", "expected true or false")
        target = route.get("model")
        if target == "primary":
            target = primary
        if target is not None:
            string(target, name + ".model")
            require(target in models, name + ".model", "unknown model")
        if not route["delegate"]:
            return {"inline": True}
        require(target is not None, name + ".model", "required for delegation")
        result["roles"][name] = [target, *chains.get(target, [])]
        return {"role": name}

    for name, route in obj(config["routes"], "routes").items():
        result["tasks"][name] = convert(route, "legacy-" + name)
    result["fallback"] = convert(config["fallback"], "legacy-fallback")
    if not result["roles"]:
        require(bool(models), "models", "must not be empty")
        result["roles"]["legacy-unused"] = list(models)
    return validate(result)


def normalize(config):
    obj(config, "$")
    if "version" in config and "schema_version" not in config:
        return migrate(config), ["v1 migrated in memory; use --migrate to save. Advisory capabilities and unused cooldown fields were not imported."]
    return validate(config), []


def catalog_models(raw):
    obj(raw, "catalog")
    require(type(raw.get("models")) is list, "catalog.models", "expected an array")
    result = {}
    for i, item in enumerate(raw["models"]):
        obj(item, f"catalog.models[{i}]")
        slug = string(item.get("slug"), f"catalog.models[{i}].slug")
        require(slug not in result, "catalog.models", f"duplicate slug: {slug}")
        efforts = item.get("supported_reasoning_levels", [])
        require(type(efforts) is list, f"catalog.{slug}.supported_reasoning_levels", "expected an array")
        values = []
        for effort in efforts:
            obj(effort, f"catalog.{slug}.effort")
            values.append(string(effort.get("effort"), f"catalog.{slug}.effort"))
        context = item.get("context_window")
        if context is not None:
            positive(context, f"catalog.{slug}.context_window")
        default = item.get("default_reasoning_level")
        if default is not None:
            string(default, f"catalog.{slug}.default_reasoning_level")
        result[slug] = {"efforts": values, "default": default, "context_window": context}
    return result


def runtime_models(raw, now=None):
    obj(raw, "runtime", {"schema_version", "observed_at", "source", "complete", "models"},
        {"schema_version", "observed_at", "source", "complete", "models"})
    require(type(raw["schema_version"]) is int and raw["schema_version"] == 1, "runtime.schema_version", "expected 1")
    require(type(raw["complete"]) is bool, "runtime.complete", "expected true or false")
    string(raw["source"], "runtime.source")
    try:
        captured = datetime.fromisoformat(string(raw["observed_at"], "runtime.observed_at").replace("Z", "+00:00"))
        require(captured.tzinfo is not None, "runtime.observed_at", "timezone required")
        age = ((now or datetime.now(timezone.utc)) - captured).total_seconds()
    except ValueError as exc:
        raise ConfigError("runtime.observed_at: invalid timestamp") from exc
    require(-60 <= age <= 3600, "runtime.observed_at", "snapshot is stale or in the future; refresh current-session evidence")
    for name, info in obj(raw["models"], "runtime.models").items():
        path = "runtime.models." + name
        obj(info, path, {"delegation", "capabilities", "reasoning_efforts", "context_window"}, {"delegation"})
        require(type(info["delegation"]) is bool, path + ".delegation", "expected true or false")
        for key in ("capabilities", "reasoning_efforts"):
            if key in info:
                strings(info[key], path + "." + key)
        if "context_window" in info:
            positive(info["context_window"], path + ".context_window")
    return raw


def choose(config, task, *, catalog=None, runtime=None, provider=None, excludes=(),
           input_tokens=0, output_tokens=0, required=(), require_verified=False,
           execution_role="dispatcher", attempt=1):
    route = config["tasks"].get(task, config["fallback"])
    base = {"task": task, "fallback": task not in config["tasks"], "delegate": False,
            "model": None, "profile": None, "role": route.get("role"), "reasoning_effort": None,
            "max_attempts": config["max_attempts"], "attempt": attempt,
            "candidates": [], "warnings": []}
    if execution_role == "worker":
        return {**base, "status": "inline", "reason": "worker_executes_assigned_task"}
    if route.get("inline"):
        return {**base, "status": "inline", "reason": "inline_policy"}
    if attempt > config["max_attempts"]:
        return {**base, "status": "unavailable", "reason": "attempt_budget_exhausted"}
    seen = set()
    for name in config["roles"][route["role"]]:
        profile = config["profiles"][name]
        model = profile["model_id"]
        info = (catalog or {}).get(model, {})
        observed = (runtime or {}).get("models", {}).get(model, {})
        reasons, warnings = [], []
        effort = profile["reasoning_effort"]
        if effort == "default":
            effort = info.get("default")
        if not profile["enabled"]:
            reasons.append("disabled")
        if model in excludes:
            reasons.append("excluded_by_dispatcher")
        if model in seen:
            reasons.append("duplicate_model_in_pool")
        # Only an eligible earlier profile should suppress another profile for the same model.
        if profile.get("provider") and profile["provider"] != provider:
            reasons.append("provider_mismatch_or_unknown")
        if catalog is not None and model not in catalog:
            reasons.append("not_in_catalog")
        if observed.get("delegation") is False or (runtime and runtime["complete"] and not observed):
            reasons.append("delegation_not_supported")
        elif not observed:
            if require_verified:
                reasons.append("delegation_unverified")
            else:
                warnings.append("delegation_unverified; host must check current tools before spawning")
        supported = observed.get("reasoning_efforts", info.get("efforts", []))
        if not effort:
            reasons.append("reasoning_default_unknown")
        elif supported and effort not in supported:
            reasons.append("reasoning_effort_unsupported")
        elif not supported:
            warnings.append("reasoning_support_unverified")
        capability_sets = [set(source["capabilities"]) for source in (profile, observed) if "capabilities" in source]
        if required:
            if not capability_sets:
                reasons.append("required_capabilities_unknown")
            elif any(not set(required) <= values for values in capability_sets):
                reasons.append("required_capabilities_missing")
        windows = [source["context_window"] for source in (profile, info, observed) if source.get("context_window") is not None]
        if input_tokens + output_tokens:
            if not windows:
                reasons.append("context_window_unknown")
            elif input_tokens + output_tokens > min(windows):
                reasons.append("context_window_too_small")
        candidate = {"profile": name, "model": model, "reasoning_effort": effort,
                     "eligible": not reasons, "reasons": reasons, "warnings": warnings}
        base["candidates"].append(candidate)
        if not reasons:
            seen.add(model)
            if base["model"] is None:
                base.update({"delegate": True, "model": model, "profile": name,
                             "reasoning_effort": effort, "warnings": warnings})
    return {**base, "status": "selected" if base["delegate"] else "unavailable",
            "reason": "first_compatible_candidate" if base["delegate"] else "no_compatible_candidate"}


def write_new(path, data):
    path = Path(path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also prevents overwriting a dangling symlink.
        with path.open("x", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            json.dump(data, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot create configuration ({exc})") from exc
