#!/usr/bin/env python3
"""Transactional installer for the task-router plugin."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLUGIN_NAME = "task-router"
EXPECTED_MARKETPLACE_NAME = "fengbochao-plugins"
EXPECTED_SOURCE_PATH = "./plugins/task-router"
REQUIRED_VERSION = "1.0.0"
MCP_CONFIG_DEFAULT = "./.mcp.json"
MCP_REQUIRED_PATHS = (
    "scripts/bootstrap_mcp.py",
    "scripts/mcp_server.py",
    "scripts/background_worker.py",
    "scripts/run_task.py",
    "scripts/task_router_runtime/cli.py",
    "scripts/task_router_runtime/service.py",
    "scripts/task_router_runtime/controller.py",
    "scripts/task_router_runtime/store.py",
    "scripts/task_router_runtime/codex_adapter.py",
    "scripts/task_router_runtime/__init__.py",
)


class InstallError(Exception):
    """An expected installation failure with a safe user-facing message."""


@dataclass
class PreparedConfig:
    temporary: Path | None
    existing: Path | None
    backup: Path | None = None
    installed: bool = False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--home",
        metavar="PATH",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--source-repo",
        metavar="PATH",
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


@contextmanager
def installation_lock(args):
    # flock is available on supported Linux/macOS/WSL installations.
    import fcntl
    home = Path(args.home).expanduser() if args.home else Path.home()
    parent = home / "plugins"
    reject_symlink(parent, "plugin parent directory")
    parent.mkdir(parents=True, exist_ok=True)
    lock_path = parent / ".task-router-install.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InstallError("another task-router installation is already running") from exc
        yield
    finally:
        os.close(descriptor)


def load_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InstallError(f"{description} not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot read valid JSON from {description}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InstallError(f"{description} must contain a JSON object: {path}")
    return value


def run(command: list[str], description: str) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except FileNotFoundError as exc:
        raise InstallError(f"cannot run {description}: executable not found") from exc
    except subprocess.CalledProcessError as exc:
        raise InstallError(f"{description} failed with exit code {exc.returncode}") from exc
    return result.stdout


def reject_symlink(path: Path, description: str) -> None:
    try:
        if path.is_symlink():
            raise InstallError(f"refusing to manage {description} because it is a symlink: {path}")
    except OSError as exc:
        raise InstallError(f"cannot inspect {description}: {path}: {exc}") from exc


def validate_no_symlinks(root: Path, description: str) -> None:
    if not root.is_dir():
        raise InstallError(f"{description} is not a directory: {root}")
    for path in root.rglob("*"):
        reject_symlink(path, f"{description} file")


def source_paths(repo: Path) -> tuple[Path, Path, Path, Path, Path]:
    marketplace = repo / ".agents" / "plugins" / "marketplace.json"
    plugin = repo / "plugins" / PLUGIN_NAME
    manifest = plugin / ".codex-plugin" / "plugin.json"
    skill = plugin / "skills" / PLUGIN_NAME
    router = skill / "scripts" / "route.py"
    bundled = skill / "routing.json"
    for path, description in (
        (marketplace, "source marketplace"),
        (manifest, "source plugin manifest"),
        (router, "router CLI"),
        (bundled, "source bundled routing config"),
    ):
        if not path.is_file():
            raise InstallError(f"{description} not found: {path}")
    validate_no_symlinks(plugin, "source plugin")
    return marketplace, plugin, manifest, router, bundled


def validate_source(repo: Path) -> tuple[Path, Path, Path]:
    marketplace_path, plugin, manifest_path, router, bundled = source_paths(repo)
    manifest = load_json(manifest_path, "source plugin manifest")
    if manifest.get("name") != PLUGIN_NAME:
        raise InstallError(f"source plugin manifest has the wrong name: {manifest.get('name')!r}")
    if not isinstance(manifest.get("version"), str) or manifest["version"].split("+")[0] != REQUIRED_VERSION:
        raise InstallError(
            f"source plugin manifest must have stable version {REQUIRED_VERSION}; "
            f"found {manifest.get('version')!r}"
        )
    if manifest.get("skills") != "./skills/":
        raise InstallError("source plugin manifest is missing its skills path")
    if not (plugin / "skills" / PLUGIN_NAME / "SKILL.md").is_file():
        raise InstallError("source plugin is missing SKILL.md")
    validate_source_mcp(plugin, manifest)

    bundled_config = load_json(bundled, "source bundled routing config")
    detect_config_version(bundled_config, str(bundled))
    validate_router_config(router, bundled, "source bundled routing config")
    return marketplace_path, plugin, router


def validate_source_mcp(plugin: Path, manifest: dict[str, Any]) -> None:
    configured = manifest.get("mcpServers")
    if configured is None:
        return
    if configured != MCP_CONFIG_DEFAULT:
        raise InstallError(
            f"source plugin mcpServers path must be {MCP_CONFIG_DEFAULT!r}; found {configured!r}"
        )
    companion = plugin / ".mcp.json"
    load_json(companion, "source MCP companion config")
    for relative in MCP_REQUIRED_PATHS:
        if not (plugin / relative).is_file():
            raise InstallError(f"source plugin is missing MCP runtime file: {plugin / relative}")


def detect_config_version(config: dict[str, Any], path: Path | str) -> int:
    schema_version = config.get("schema_version")
    legacy_version = config.get("version")
    if type(schema_version) is int and schema_version == 2 and legacy_version is None:
        return 2
    if schema_version is None and type(legacy_version) is int and legacy_version == 1:
        return 1
    raise InstallError(f"unsupported or ambiguous routing config version: {path}")


def validate_router_config(router: Path, config: Path, description: str) -> None:
    output = run(
        [sys.executable, str(router), "--validate", "--config", str(config)],
        f"validation of {description}",
    )
    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise InstallError(f"router returned invalid JSON while validating {description}") from exc
    if not isinstance(result, dict) or result.get("status") != "valid":
        raise InstallError(f"router rejected {description}")


def validate_repo_marketplace(path: Path) -> str:
    marketplace = load_json(path, "source marketplace")
    name = marketplace.get("name")
    if name != EXPECTED_MARKETPLACE_NAME:
        raise InstallError(
            f"source marketplace name mismatch: expected {EXPECTED_MARKETPLACE_NAME!r}, found {name!r}"
        )
    entries = marketplace.get("plugins")
    if not isinstance(entries, list):
        raise InstallError("source marketplace plugins must be an array")
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME]
    if len(matches) != 1:
        raise InstallError(f"source marketplace must contain exactly one {PLUGIN_NAME} entry")
    source = matches[0].get("source")
    if not isinstance(source, dict) or source.get("source") != "local" or source.get("path") != EXPECTED_SOURCE_PATH:
        raise InstallError("source marketplace task-router source mapping is invalid")
    return name


def validate_personal_marketplace(path: Path) -> str | None:
    if not path.exists():
        if path.is_symlink():
            raise InstallError(f"refusing to manage symlink marketplace path: {path}")
        return None
    reject_symlink(path, "personal marketplace")
    marketplace = load_json(path, "personal marketplace")
    name = marketplace.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise InstallError("personal marketplace has no valid name")
    entries = marketplace.get("plugins")
    if not isinstance(entries, list):
        raise InstallError("personal marketplace plugins must be an array")
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME]
    if len(matches) > 1:
        raise InstallError(f"personal marketplace contains multiple {PLUGIN_NAME} entries")
    if not matches:
        return None
    source = matches[0].get("source")
    if (
        not isinstance(source, dict)
        or source.get("source") != "local"
        or source.get("path") != EXPECTED_SOURCE_PATH
    ):
        raise InstallError(
            f"personal marketplace has a custom {PLUGIN_NAME} entry whose source mapping does not match this installer"
        )
    return name


def plugin_content_hash(plugin: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in plugin.rglob("*") if item.is_file()):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        digest.update(str(path.relative_to(plugin)).encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def unique_path(base: Path, label: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return base / f"{label}.{timestamp}.{os.getpid()}.{uuid.uuid4().hex[:12]}"


def stage_plugin(source: Path, parent: Path, backup_root: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(parent, "plugin parent directory")
    stage = unique_path(parent, f".{PLUGIN_NAME}.stage")
    try:
        shutil.copytree(source, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        manifest_path = stage / ".codex-plugin" / "plugin.json"
        manifest = load_json(manifest_path, "staged plugin manifest")
        if "+" not in manifest["version"]:
            manifest["version"] = f"{REQUIRED_VERSION}+codex.{plugin_content_hash(source)[:16]}"
        atomic_json_write(manifest_path, manifest)
    except Exception:
        if stage.exists():
            failed = unique_path(backup_root, f"{PLUGIN_NAME}.stage-failed")
            os.replace(stage, failed)
        raise
    return stage


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        unlink_owned(temporary)
        raise


def unlink_owned(path: Path) -> None:
    try:
        if path.is_file() or path.is_symlink():
            path.unlink()
    except FileNotFoundError:
        pass


def swap_directory(stage: Path, destination: Path, backup_root: Path) -> tuple[Path | None, Path | None]:
    backup: Path | None = None
    if destination.exists() or destination.is_symlink():
        reject_symlink(destination, "managed plugin destination")
        if not destination.is_dir():
            raise InstallError(f"managed plugin destination is not a directory: {destination}")
        backup = unique_path(backup_root, f"{PLUGIN_NAME}.backup")
        os.replace(destination, backup)
    try:
        os.replace(stage, destination)
    except Exception:
        if backup is not None and not destination.exists():
            os.replace(backup, destination)
        raise
    return backup, None


def user_config_path(home: Path, explicit_home: bool) -> Path:
    if explicit_home:
        return home / ".config" / "task-router" / "routing.json"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        root = Path(xdg)
        if not root.is_absolute():
            raise InstallError("XDG_CONFIG_HOME must be an absolute path")
    else:
        root = home / ".config"
    return root / "task-router" / "routing.json"


def prepare_user_config(config: Path, bundled: Path, router: Path) -> PreparedConfig:
    reject_symlink(config, "persistent routing config")
    if config.exists() and not config.is_file():
        raise InstallError(f"persistent routing config is not a regular file: {config}")
    existing_data = None
    if config.exists():
        existing_data = load_json(config, "persistent routing config")
        detect_config_version(existing_data, config)

    config.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(config.parent, "persistent routing config directory")
    output = unique_path(config.parent, ".routing.json.stage")
    try:
        if existing_data is None:
            run(
                [sys.executable, str(router), "--migrate", "--config", str(bundled), "--output", str(output)],
                "routing config initialization",
            )
        elif detect_config_version(existing_data, config) == 2:
            validate_router_config(router, config, "persistent routing config")
            unlink_owned(output)
            return PreparedConfig(None, config)
        else:
            run(
                [sys.executable, str(router), "--migrate", "--config", str(config), "--output", str(output)],
                "routing config migration",
            )
        migrated = load_json(output, "migrated routing config")
        if detect_config_version(migrated, output) != 2:
            raise InstallError("router migration did not produce schema version 2")
        validate_router_config(router, output, "migrated routing config")
        return PreparedConfig(output, config)
    except Exception:
        unlink_owned(output)
        raise


def old_bundled_config(destination: Path) -> Path | None:
    path = destination / "skills" / PLUGIN_NAME / "routing.json"
    return path if path.is_file() else None


def prepare_config_from_old_bundle(config: Path, destination: Path, router: Path) -> PreparedConfig:
    bundle = old_bundled_config(destination)
    if bundle is None:
        return prepare_default_config(config, router)
    data = load_json(bundle, "old bundled routing config")
    if detect_config_version(data, bundle) != 1:
        return prepare_user_config(config, bundle, router)
    reject_symlink(config, "persistent routing config")
    config.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(config.parent, "persistent routing config directory")
    output = unique_path(config.parent, ".routing.json.stage")
    try:
        run(
            [sys.executable, str(router), "--migrate", "--config", str(bundle), "--output", str(output)],
            "old bundled routing config migration",
        )
        migrated = load_json(output, "migrated routing config")
        if detect_config_version(migrated, output) != 2:
            raise InstallError("router migration did not produce schema version 2")
        validate_router_config(router, output, "migrated routing config")
        return PreparedConfig(output, config)
    except Exception:
        unlink_owned(output)
        raise


def prepare_default_config(config: Path, router: Path) -> PreparedConfig:
    reject_symlink(config, "persistent routing config")
    config.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(config.parent, "persistent routing config directory")
    output = unique_path(config.parent, ".routing.json.stage")
    try:
        run(
            [sys.executable, str(router), "--init-config", "--output", str(output)],
            "routing config initialization",
        )
        initialized = load_json(output, "initialized routing config")
        if detect_config_version(initialized, output) != 2:
            raise InstallError("router initialization did not produce schema version 2")
        validate_router_config(router, output, "initialized routing config")
        return PreparedConfig(output, config)
    except Exception:
        unlink_owned(output)
        raise


def commit_config(prepared: PreparedConfig, config: Path, backup_root: Path) -> None:
    if prepared.temporary is None:
        return
    backup: Path | None = None
    if config.exists() or config.is_symlink():
        # XDG_CONFIG_HOME may be on another filesystem; keep config renames local.
        backup = unique_path(config.parent, "routing.json.backup")
        os.replace(config, backup)
        prepared.backup = backup
    try:
        os.replace(prepared.temporary, config)
    except Exception:
        if backup is not None and not config.exists():
            os.replace(backup, config)
            prepared.backup = None
        raise
    prepared.installed = True


def rollback_config(prepared: PreparedConfig, config: Path, backup_root: Path) -> Path | None:
    artifact: Path | None = None
    if prepared.installed:
        if config.exists() or config.is_symlink():
            artifact = unique_path(config.parent, "routing.json.failed")
            os.replace(config, artifact)
        if prepared.backup is not None:
            os.replace(prepared.backup, config)
            prepared.backup = None
    else:
        if prepared.temporary is not None:
            unlink_owned(prepared.temporary)
    return artifact


def rollback_directory(destination: Path, backup: Path | None, backup_root: Path) -> Path | None:
    artifact: Path | None = None
    if destination.exists() or destination.is_symlink():
        artifact = unique_path(backup_root, f"{PLUGIN_NAME}.failed")
        os.replace(destination, artifact)
    if backup is not None:
        os.replace(backup, destination)
    return artifact


def register_or_install(
    personal_marketplace_exists: bool,
    repo: Path,
    marketplace_name: str,
) -> None:
    selector = f"{PLUGIN_NAME}@{marketplace_name}"
    if not personal_marketplace_exists:
        run(["codex", "plugin", "marketplace", "add", str(repo)], "Codex marketplace registration")
    run(["codex", "plugin", "add", selector], f"Codex installation of {selector}")


def install(args: argparse.Namespace) -> tuple[Path | None, Path | None, Path | None, Path | None]:
    if sys.version_info < (3, 11):
        raise InstallError("Python 3.11 or newer is required")
    if shutil.which("codex") is None:
        raise InstallError("codex CLI not found in PATH")
    if args.home is not None and os.environ.get("TASK_ROUTER_INSTALL_TESTING") != "1":
        raise InstallError("--home is for fake-CLI tests only; it does not isolate the Codex process")
    if os.environ.get("TASK_ROUTER_CONFIG"):
        raise InstallError("TASK_ROUTER_CONFIG overrides the managed user config; unset it for installation and migrate that file explicitly")
    repo = Path(args.source_repo or Path(__file__).resolve().parent.parent).resolve()
    explicit_home = args.home is not None
    home = Path(args.home).expanduser().resolve() if explicit_home else Path.home().resolve()
    destination = home / "plugins" / PLUGIN_NAME
    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
    config = user_config_path(home, explicit_home)
    reject_symlink(config, "persistent routing config")
    reject_symlink(config.parent, "persistent routing config directory")
    backup_root = destination.parent / ".task-router-backups"

    source_marketplace, plugin, router = validate_source(repo)
    repo_marketplace_name = validate_repo_marketplace(source_marketplace)
    personal_marketplace = validate_personal_marketplace(marketplace_path)
    personal_exists = personal_marketplace is not None
    marketplace_name = personal_marketplace or repo_marketplace_name

    destination_parent = destination.parent
    reject_symlink(destination_parent, "plugin parent directory")
    if destination.exists() or destination.is_symlink():
        reject_symlink(destination, "managed plugin destination")
        if not destination.is_dir():
            raise InstallError(f"managed plugin destination is not a directory: {destination}")
    if personal_exists and not destination.exists():
        raise InstallError(
            f"personal marketplace references {destination}, but that plugin directory is absent"
        )
    if not personal_exists and destination.exists():
        raise InstallError(
            f"{destination} already exists but the personal marketplace has no matching local entry"
        )

    prepared_config: PreparedConfig | None = None
    directory_backup: Path | None = None
    config_artifact: Path | None = None
    directory_artifact: Path | None = None
    stage: Path | None = None
    directory_installed = False
    backup_root.mkdir(parents=True, exist_ok=True)
    reject_symlink(backup_root, "installer backup directory")

    try:
        # Prove these CLI commands exist before replacing either plugin or configuration.
        run(["codex", "plugin", "add", "--help"], "Codex plugin command check")
        if not personal_exists:
            run(["codex", "plugin", "marketplace", "add", "--help"], "Codex marketplace command check")
        if personal_exists:
            old_bundle = destination / "skills" / PLUGIN_NAME / "routing.json"
            if old_bundle.is_file() and config.exists():
                existing = load_json(config, "persistent routing config")
                detect_config_version(existing, config)
                if detect_config_version(existing, config) == 2:
                    validate_router_config(router, config, "persistent routing config")
                    prepared_config = PreparedConfig(None, config)
                else:
                    prepared_config = prepare_user_config(config, old_bundle, router)
            elif old_bundle.is_file():
                prepared_config = prepare_config_from_old_bundle(config, destination, router)
            else:
                prepared_config = prepare_user_config(
                    config,
                    repo / "plugins" / PLUGIN_NAME / "skills" / PLUGIN_NAME / "routing.json",
                    router,
                )
            stage = stage_plugin(plugin, destination_parent, backup_root)
            directory_backup, _ = swap_directory(stage, destination, backup_root)
            stage = None
            directory_installed = True
            if prepared_config is not None:
                commit_config(prepared_config, config, backup_root)
        else:
            prepared_config = prepare_user_config(
                config,
                repo / "plugins" / PLUGIN_NAME / "skills" / PLUGIN_NAME / "routing.json",
                router,
            )
            commit_config(prepared_config, config, backup_root)

        register_or_install(personal_exists, repo, marketplace_name)
    except BaseException as exc:
        recovery_errors = []
        if prepared_config is not None:
            try:
                config_artifact = rollback_config(prepared_config, config, backup_root)
            except Exception as recovery:
                recovery_errors.append(f"configuration recovery failed: {recovery}")
        if directory_installed:
            try:
                directory_artifact = rollback_directory(destination, directory_backup, backup_root)
            except Exception as recovery:
                recovery_errors.append(f"plugin recovery failed: {recovery}")
        elif stage is not None:
            directory_artifact = unique_path(backup_root, f"{PLUGIN_NAME}.stage-failed")
            os.replace(stage, directory_artifact)
        details = [str(exc) or type(exc).__name__]
        if config_artifact:
            details.append(f"failed config retained: {config_artifact}")
        if directory_artifact:
            details.append(f"failed plugin retained: {directory_artifact}")
        if not personal_exists:
            details.append("a marketplace registration may remain; inspect codex plugin marketplace list")
        details.extend(recovery_errors)
        raise InstallError("; ".join(details)) from exc

    print(f"installed {PLUGIN_NAME} {REQUIRED_VERSION}")
    print(f"active user configuration: {config}")
    if not personal_exists:
        print(f"marketplace source: {repo}; keep this repository at this path for updates")
    if directory_backup is not None:
        print(f"previous plugin backup: {directory_backup}")
    if prepared_config is not None and prepared_config.backup is not None:
        print(f"previous routing config backup: {prepared_config.backup}")
    if config_artifact is not None:
        print(f"failed routing config retained at: {config_artifact}")
    if directory_artifact is not None:
        print(f"failed plugin directory retained at: {directory_artifact}")
    print("start a new Codex thread to load the updated plugin")
    return directory_backup, config_artifact, directory_artifact, prepared_config.backup if prepared_config else None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        with installation_lock(args):
            install(args)
    except (InstallError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
