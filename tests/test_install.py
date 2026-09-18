#!/usr/bin/env python3
"""Isolated installer tests using a fixture source and a fake Codex CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parent.parent / "scripts" / "install.py"
PLUGIN = "task-router"

V1 = {
    "version": 1,
    "routes": {"discussion": {"delegate": True, "model": "old-model"}},
}

V2 = {
    "schema_version": 2,
    "profiles": {},
    "tasks": {},
}

SOURCE_MANIFEST = {
    "name": PLUGIN,
    "version": "1.0.0",
    "skills": "./skills/",
}

MARKETPLACE = {
    "name": "fengbochao-plugins",
    "interface": {"displayName": "Fengbochao Plugins"},
    "plugins": [
        {
            "name": PLUGIN,
            "source": {"source": "local", "path": "./plugins/task-router"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        }
    ],
}

FAKE_ROUTER = r'''#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--validate", action="store_true")
parser.add_argument("--migrate", action="store_true")
parser.add_argument("--init-config", action="store_true")
parser.add_argument("--config", type=Path)
parser.add_argument("--output", type=Path)
args = parser.parse_args()

def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise SystemExit(2)

if args.validate:
    value = read(args.config)
    if value.get("invalid") is True:
        print(json.dumps({"ok": False, "error": "fixture invalid config"}))
        raise SystemExit(1)
    print(json.dumps({"status": "valid"}))
elif args.migrate:
    value = read(args.config)
    if value.get("schema_version") == 2:
        migrated = value
    elif value.get("version") == 1:
        migrated = {"schema_version": 2, "migrated_from": value}
    else:
        raise SystemExit(2)
    args.output.write_text(json.dumps(migrated), encoding="utf-8")
elif args.init_config:
    args.output.write_text(json.dumps({"schema_version": 2, "default": True}), encoding="utf-8")
'''

FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if "--help" in sys.argv:
    raise SystemExit(0)

log = Path(os.environ["FAKE_CODEX_LOG"])
command = " ".join(sys.argv[1:])
with log.open("a", encoding="utf-8") as stream:
    protected = {name: os.environ.get(name) for name in ("HOME", "CODEX_HOME")}
    stream.write(json.dumps({"command": command, "cwd": Path.cwd().name, "protected": protected}) + "\n")

if sys.argv[1:3] == ["plugin", "add"]:
    if os.environ.get("FAKE_CODEX_FAIL_ADD") == "1":
        print("fixture plugin failure", file=sys.stderr)
        raise SystemExit(17)
'''


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.root = root
        self.source = root / "source"
        self.home = root / "home"
        self.bin = root / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        self.make_source()
        self.make_fake_cli()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_source(self) -> None:
        plugin = self.source / "plugins" / PLUGIN
        skill = plugin / "skills" / PLUGIN
        (plugin / ".codex-plugin").mkdir(parents=True)
        skill.mkdir(parents=True)
        (self.source / ".agents" / "plugins").mkdir(parents=True)
        self.write_json(plugin / ".codex-plugin" / "plugin.json", SOURCE_MANIFEST)
        self.write_json(self.source / ".agents" / "plugins" / "marketplace.json", MARKETPLACE)
        self.write_json(skill / "routing.json", V2)
        (skill / "SKILL.md").write_text("# Fixture skill\n", encoding="utf-8")
        router = skill / "scripts" / "route.py"
        router.parent.mkdir()
        router.write_text(FAKE_ROUTER, encoding="utf-8")
        router.chmod(0o755)

    def make_fake_cli(self) -> None:
        self.log = self.root / "codex.log"
        codex = self.bin / "codex"
        codex.write_text(FAKE_CODEX, encoding="utf-8")
        codex.chmod(0o755)

    def enable_source_mcp(self) -> Path:
        plugin = self.source / "plugins" / PLUGIN
        manifest = dict(SOURCE_MANIFEST)
        manifest["mcpServers"] = "./.mcp.json"
        self.write_json(plugin / ".codex-plugin" / "plugin.json", manifest)
        companion = plugin / ".mcp.json"
        self.write_json(companion, {"mcpServers": {"task-router": {}}})
        for relative in (
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
        ):
            path = plugin / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture MCP runtime\n", encoding="utf-8")
        return companion

    def write_json(self, path: Path, value: dict) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def personal_home(self, bundled=V2, marketplace_name="personal") -> None:
        destination = self.home / "plugins" / PLUGIN
        skill = destination / "skills" / PLUGIN
        skill.mkdir(parents=True)
        (self.home / ".agents" / "plugins").mkdir(parents=True)
        self.write_json(skill / "routing.json", bundled)
        self.write_json(
            self.home / ".agents" / "plugins" / "marketplace.json",
            {
                "name": marketplace_name,
                "plugins": [
                    {
                        "name": PLUGIN,
                        "source": {"source": "local", "path": "./plugins/task-router"},
                    }
                ],
            },
        )

    def run_installer(self, fail_add=False) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop("TASK_ROUTER_CONFIG", None)
        self.protected = {name: environment.get(name) for name in ("HOME", "CODEX_HOME")}
        environment.update(
            {
                "PATH": f"{self.bin}:{environment.get('PATH', '')}",
                "FAKE_CODEX_LOG": str(self.log),
                "FAKE_CODEX_FAIL_ADD": "1" if fail_add else "0",
                "TASK_ROUTER_INSTALL_TESTING": "1",
                "XDG_CONFIG_HOME": str(self.root / "xdg-config"),
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                str(INSTALLER),
                "--plugin-only",
                "--home",
                str(self.home),
                "--source-repo",
                str(self.source),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        return result

    def commands(self) -> list[list[str]]:
        return [json.loads(line)["command"].split() for line in self.log.read_text().splitlines()]

    def protected_values(self) -> list[dict]:
        return [json.loads(line)["protected"] for line in self.log.read_text().splitlines()]

    def test_fresh_install_uses_repo_marketplace_root(self) -> None:
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.commands(),
            [
                ["plugin", "marketplace", "add", str(self.source)],
                ["plugin", "add", "task-router@fengbochao-plugins"],
            ],
        )
        self.assertTrue(self.protected_values())
        self.assertTrue(all(value == self.protected for value in self.protected_values()))
        config = self.home / ".config" / "task-router" / "routing.json"
        self.assertEqual(json.loads(config.read_text())["schema_version"], 2)

    def test_current_repository_source_passes_fake_install(self) -> None:
        self.source = Path(__file__).resolve().parent.parent
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.home / ".config" / "task-router" / "routing.json"
        self.assertEqual(json.loads(config.read_text())["schema_version"], 2)

    def test_existing_personal_install_stages_cache_busted_copy(self) -> None:
        self.personal_home(bundled=V1)
        old_path = self.home / "plugins" / PLUGIN / "skills" / PLUGIN / "routing.json"
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commands(), [["plugin", "add", "task-router@personal"]])
        manifest = json.loads(
            (self.home / "plugins" / PLUGIN / ".codex-plugin" / "plugin.json").read_text()
        )
        self.assertTrue(manifest["version"].startswith("1.0.0+codex."))
        backups = list((self.home / "plugins/.task-router-backups").glob("task-router.backup.*"))
        self.assertEqual(len(backups), 1)
        self.assertIn("version", json.loads(backups[0].joinpath("skills", PLUGIN, "routing.json").read_text()))

    def test_mcp_source_requires_valid_companion(self) -> None:
        companion = self.enable_source_mcp()
        companion.write_text("{not json", encoding="utf-8")
        self.personal_home()
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read valid JSON from source MCP companion config", result.stderr)
        self.assertFalse(self.log.exists())

    def test_mcp_source_requires_companion(self) -> None:
        companion = self.enable_source_mcp()
        companion.unlink()
        self.personal_home()
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("source MCP companion config not found", result.stderr)
        self.assertFalse(self.log.exists())

    def test_mcp_source_requires_default_companion_reference(self) -> None:
        self.enable_source_mcp()
        plugin = self.source / "plugins" / PLUGIN
        manifest = json.loads((plugin / ".codex-plugin" / "plugin.json").read_text())
        manifest["mcpServers"] = "./mcp/custom.json"
        self.write_json(plugin / ".codex-plugin" / "plugin.json", manifest)
        self.personal_home()
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("mcpServers path must be './.mcp.json'", result.stderr)
        self.assertFalse(self.log.exists())

    def test_mcp_source_requires_runtime(self) -> None:
        self.enable_source_mcp()
        plugin = self.source / "plugins" / PLUGIN
        runtime = [
            plugin / "scripts" / "bootstrap_mcp.py",
            plugin / "scripts" / "mcp_server.py",
            plugin / "scripts" / "run_task.py",
            plugin / "scripts" / "task_router_runtime" / "cli.py",
            plugin / "scripts" / "task_router_runtime" / "controller.py",
            plugin / "scripts" / "task_router_runtime" / "store.py",
            plugin / "scripts" / "task_router_runtime" / "codex_adapter.py",
            plugin / "scripts" / "task_router_runtime" / "__init__.py",
        ]
        for path in runtime:
            with self.subTest(path=path.relative_to(plugin)):
                original = path.read_text(encoding="utf-8")
                path.unlink()
                result = self.run_installer()
                self.assertEqual(result.returncode, 1)
                self.assertIn("source plugin is missing MCP runtime file", result.stderr)
                path.write_text(original, encoding="utf-8")

    def test_copied_plugin_alone_does_not_need_mcp_files(self) -> None:
        self.personal_home()
        destination = self.home / "plugins" / PLUGIN
        (destination / ".codex-plugin").mkdir(parents=True)
        self.write_json(
            destination / ".codex-plugin" / "plugin.json",
            {**SOURCE_MANIFEST, "mcpServers": "./.mcp.json"},
        )
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(destination.joinpath("skills", PLUGIN, "routing.json").is_file())

    def test_update_preserves_existing_v2_user_config(self) -> None:
        self.personal_home()
        config = self.home / ".config" / "task-router" / "routing.json"
        config.parent.mkdir(parents=True)
        preserved = {"schema_version": 2, "user": "preserve-me"}
        self.write_json(config, preserved)
        original_bytes = config.read_bytes()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_bytes(), original_bytes)

    def test_destination_symlink_is_rejected_without_replacing_it(self) -> None:
        self.personal_home()
        destination = self.home / "plugins" / PLUGIN
        real = self.home / "plugins" / "real-task-router"
        destination.rename(real)
        destination.symlink_to(real)
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("managed plugin destination", result.stderr)
        self.assertTrue(destination.is_symlink())
        self.assertTrue(real.joinpath("skills", PLUGIN, "routing.json").is_file())

    def test_config_symlink_is_rejected_without_replacing_it(self) -> None:
        target = self.home / "private-routing.json"
        self.write_json(target, V2)
        config = self.home / ".config" / "task-router" / "routing.json"
        config.parent.mkdir(parents=True)
        config.symlink_to(target)
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("persistent routing config", result.stderr)
        self.assertTrue(config.is_symlink())
        self.assertEqual(json.loads(target.read_text()), V2)
        self.assertFalse(self.log.exists())

    def test_old_bundled_v1_migrates_to_private_config_when_absent(self) -> None:
        self.personal_home(bundled=V1)
        config = self.home / ".config" / "task-router" / "routing.json"
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        migrated = json.loads(config.read_text())
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["migrated_from"], V1)

    def test_external_v1_migration_backs_up_original(self) -> None:
        self.personal_home()
        config = self.home / ".config" / "task-router" / "routing.json"
        config.parent.mkdir(parents=True)
        self.write_json(config, V1)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(config.read_text())["migrated_from"], V1)
        backups = list(config.parent.glob("routing.json.backup.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), V1)

    def test_malformed_user_config_fails_before_managed_changes(self) -> None:
        self.personal_home()
        marketplace = self.home / ".agents" / "plugins" / "marketplace.json"
        marketplace_before = marketplace.read_bytes()
        config = self.home / ".config" / "task-router" / "routing.json"
        config.parent.mkdir(parents=True)
        config.write_text("{not json", encoding="utf-8")
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read valid JSON", result.stderr)
        self.assertEqual(marketplace.read_bytes(), marketplace_before)
        self.assertEqual(config.read_text(), "{not json")
        self.assertFalse(self.log.exists())

    def test_nonmatching_personal_entry_fails_without_mutations(self) -> None:
        self.personal_home()
        marketplace = self.home / ".agents" / "plugins" / "marketplace.json"
        value = json.loads(marketplace.read_text())
        value["plugins"][0]["source"]["path"] = "./other/task-router"
        self.write_json(marketplace, value)
        before = marketplace.read_bytes()
        destination = self.home / "plugins" / PLUGIN
        destination_before = list(destination.rglob("*"))
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("source mapping", result.stderr)
        self.assertEqual(marketplace.read_bytes(), before)
        self.assertEqual(list(destination.rglob("*")), destination_before)
        self.assertFalse(self.log.exists())

    def test_failed_codex_add_rolls_back_directory_and_config(self) -> None:
        self.personal_home(bundled=V1)
        destination = self.home / "plugins" / PLUGIN
        config = self.home / ".config" / "task-router" / "routing.json"
        config.parent.mkdir(parents=True)
        self.write_json(config, V1)
        result = self.run_installer(fail_add=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Codex installation", result.stderr)
        self.assertTrue(destination.is_dir())
        self.assertEqual(json.loads(destination.joinpath("skills", PLUGIN, "routing.json").read_text()), V1)
        self.assertEqual(json.loads(config.read_text()), V1)
        backups = self.home / "plugins/.task-router-backups"
        self.assertTrue(list(backups.glob("task-router.failed.*")))
        self.assertTrue(list(config.parent.glob("routing.json.failed.*")))

    def test_repeat_personal_install_is_supported(self) -> None:
        self.personal_home()
        first = self.run_installer()
        second = self.run_installer()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self.commands()), 2)
        backups = self.home / "plugins/.task-router-backups"
        self.assertEqual(len(list(backups.glob("task-router.backup.*"))), 2)

    def test_valid_v2_config_symlink_in_personal_install_is_rejected(self):
        self.personal_home()
        target = self.home / "original.json"
        self.write_json(target, V2)
        config = self.home / ".config/task-router/routing.json"
        config.parent.mkdir(parents=True)
        config.symlink_to(target)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(config.is_symlink())
        self.assertFalse(self.log.exists())

    def test_existing_v2_bundle_without_external_config_is_preserved(self):
        original = {"schema_version": 2, "custom": "retain old bundle"}
        self.personal_home(bundled=original)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.home / ".config/task-router/routing.json"
        self.assertEqual(json.loads(config.read_text()), original)

    def test_real_router_rejects_malformed_source_policy_without_replacing_old_plugin(self):
        import shutil
        real_source = Path(__file__).resolve().parent.parent / "plugins" / PLUGIN
        target = self.source / "plugins" / PLUGIN
        shutil.copytree(real_source, target, dirs_exist_ok=True)
        bundled = target / "skills" / PLUGIN / "routing.json"
        bad = json.loads(bundled.read_text())
        bad["profiles"]["coding-main"]["enabled"] = "false"
        self.write_json(bundled, bad)
        self.personal_home()
        before = (self.home / "plugins/task-router/skills/task-router/routing.json").read_bytes()
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.home / "plugins/task-router/skills/task-router/routing.json").read_bytes(), before)
        self.assertFalse(self.log.exists())

    def test_real_router_migrates_personal_policy_and_rolls_back_on_codex_failure(self):
        self.source = Path(__file__).resolve().parent.parent
        old = {"version": 1, "defaults": {"primary": "old-model"}, "models": {"old-model": {}},
               "routes": {"edit": {"delegate": True, "model": "old-model"}},
               "fallback": {"delegate": False}}
        self.personal_home(bundled=old)
        config = self.home / ".config/task-router/routing.json"
        first = self.run_installer(fail_add=True)
        self.assertNotEqual(first.returncode, 0)
        self.assertFalse(config.exists())
        self.assertIn("failed plugin retained", first.stderr)
        second = self.run_installer()
        self.assertEqual(second.returncode, 0, second.stderr)
        migrated = json.loads(config.read_text())
        self.assertEqual(migrated["roles"]["legacy-edit"], ["old-model"])
        before = config.read_bytes()
        again = self.run_installer()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(config.read_bytes(), before)

    def test_concurrent_install_is_rejected(self):
        import fcntl
        parent = self.home / "plugins"
        parent.mkdir()
        with (parent / ".task-router-install.lock").open("w") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already running", result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
