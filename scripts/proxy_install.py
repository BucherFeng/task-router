"""Prepare and deploy the per-user proxy before switching a Codex provider."""

import copy
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SERVICE = "task-router-proxy"


class ProxyInstallError(RuntimeError):
    pass


def run_service(systemctl, *args, check=True):
    try:
        result = subprocess.run([systemctl, "--user", *args], capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProxyInstallError(f"systemd user command failed ({type(exc).__name__})") from exc
    if check and result.returncode:
        raise ProxyInstallError(f"systemctl --user {' '.join(args)} failed (exit {result.returncode})")
    return result.returncode == 0


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_url(value):
    if not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
        raise ProxyInstallError("provider base_url must be an HTTP(S) URL")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ProxyInstallError("invalid provider base_url") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ProxyInstallError("base_url must contain an HTTP(S) origin and optional API path, without credentials or query")
    return parts


def set_provider_url(text, provider, value):
    """Locate the selected TOML table; verify the edit by parsing the full result."""
    document = tomllib.loads(text)
    expected = copy.deepcopy(document)
    expected["model_providers"][provider]["base_url"] = value
    expected["model_providers"][provider]["supports_websockets"] = False
    lines = text.splitlines(keepends=True)
    active = False
    changed = False
    websocket = False
    section_end = len(lines)
    for index, line in enumerate(lines):
        if line.lstrip().startswith("["):
            if active:
                section_end = index
                break
            try:
                table = tomllib.loads(line)
            except tomllib.TOMLDecodeError:
                active = False
                continue
            active = table == {"model_providers": {provider: {}}}
        elif active:
            match = re.match(r'^(\s*base_url\s*=\s*)(.*?)(\r?\n)?$', line)
            if match:
                # Match supported one-line values using TOML, including single quotes and comments.
                try:
                    parsed = tomllib.loads(line)
                except tomllib.TOMLDecodeError as exc:
                    raise ProxyInstallError("base_url must be a one-line TOML string") from exc
                if set(parsed) != {"base_url"}:
                    raise ProxyInstallError("unexpected base_url assignment")
                lines[index] = match.group(1) + json.dumps(value) + "\n"
                changed = True
            elif re.match(r'^\s*supports_websockets\s*=', line):
                lines[index] = "supports_websockets = false\n"
                websocket = True
    if not changed:
        raise ProxyInstallError("could not locate the selected provider's base_url assignment")
    if not websocket:
        if section_end and not lines[section_end - 1].endswith("\n"):
            lines[section_end - 1] += "\n"
        lines.insert(section_end, "supports_websockets = false\n")
    result = "".join(lines)
    if tomllib.loads(result) != expected:
        raise ProxyInstallError("provider update changed unexpected TOML settings")
    return result


def service_quote(value):
    value = str(value)
    if any(char in value for char in "\r\n\x00"):
        raise ProxyInstallError("invalid newline in service argument")
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


class ProxyDeployment:
    def __init__(self, *, home, repo, systemctl, port=8787, codex_home=None,
                 upstream_override=None, glm_fallback="gpt-6-astra", gpt_fallback="glm-5.3"):
        if not 1 <= port <= 65535:
            raise ProxyInstallError("proxy port must be 1..65535")
        if not glm_fallback.startswith("gpt-") or not gpt_fallback.startswith("glm-"):
            raise ProxyInstallError("fallback models must belong to the opposite GPT/GLM family")
        self.home, self.repo, self.systemctl, self.port = Path(home), Path(repo), systemctl, port
        self.config_path = Path(codex_home or self.home / ".codex") / "config.toml"
        self.script = self.home / ".local/share/task-router/model_proxy.py"
        self.unit = self.home / ".config/systemd/user/task-router-proxy.service"
        self.state_dir = self.home / ".local/state/task-router"
        self.marker = self.state_dir / "original-base-url"
        self.metadata = self.state_dir / "proxy-install.json"
        self.health_url = f"http://127.0.0.1:{port}/health"
        self.saved = {}
        self.started = False
        self.glm_fallback, self.gpt_fallback = glm_fallback, gpt_fallback
        for path in (self.config_path, self.script, self.unit, self.marker, self.metadata):
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ProxyInstallError(f"managed path must not be a symlink: {path}")
        try:
            self.initial = self.config_path.read_bytes()
            self.config = tomllib.loads(self.initial.decode("utf-8"))
            self.provider_name = self.config["model_provider"]
            provider = self.config["model_providers"][self.provider_name]
            url = provider["base_url"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ProxyInstallError("configure a Codex HTTP Responses provider with model_provider and base_url before installing") from exc
        if provider.get("wire_api", "responses") != "responses":
            raise ProxyInstallError("complete installation requires wire_api = responses")
        parsed = validate_url(url)
        is_proxy = parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == port
        if is_proxy:
            if upstream_override:
                url = upstream_override
            elif self.marker.is_file():
                url = self.marker.read_text(encoding="utf-8").strip()
            else:
                raise ProxyInstallError("existing local proxy has no upstream marker; provide --upstream with the original API base URL")
        elif upstream_override:
            url = upstream_override
        parsed = validate_url(url)
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == port:
            raise ProxyInstallError("upstream points back to the proxy")
        self.upstream = url.rstrip("/")
        # Preserve the entire provider path; the proxy forwards request paths unchanged.
        self.origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        self.local_url = f"http://127.0.0.1:{port}" + parsed.path.rstrip("/")
        set_provider_url(self.initial.decode("utf-8"), self.provider_name, self.local_url)
        self.source = self.repo / "scripts/model_proxy.py"
        if not self.source.is_file():
            raise ProxyInstallError("release is missing scripts/model_proxy.py")
        compile(self.source.read_bytes(), str(self.source), "exec")
        run_service(systemctl, "show-environment")
        self.was_active = run_service(systemctl, "is-active", SERVICE, check=False)
        self.was_enabled = run_service(systemctl, "is-enabled", SERVICE, check=False)
        if not self.was_active:
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError as exc:
                    raise ProxyInstallError(f"proxy port {port} is occupied; choose --proxy-port") from exc
        elif not self.unit.is_file():
            raise ProxyInstallError("active proxy is not managed by the expected user service file")
        self.instance_id = hashlib.sha256(os.urandom(32)).hexdigest()

    def _replace(self, path, data):
        if path not in self.saved:
            self.saved[path] = path.read_bytes() if path.exists() else None
        write_atomic(path, data)

    def install(self, backup_root):
        backup_root = Path(backup_root)
        backup_root.mkdir(parents=True, exist_ok=True)
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._replace(self.script, self.source.read_bytes())
            command = [sys.executable, "-B", str(self.script), "--listen", f"127.0.0.1:{self.port}",
                       "--upstream", self.origin, "--glm-fallback", self.glm_fallback,
                       "--gpt-fallback", self.gpt_fallback, "--fail-status", "503", "--cooldown-seconds", "600",
                       "--state-file", str(self.state_dir / "proxy-state.json"),
                       "--access-log", str(self.state_dir / "proxy-access.jsonl"), "--instance-id", self.instance_id]
            unit = ("[Unit]\nDescription=Task Router model proxy\nAfter=network-online.target\n\n[Service]\n"
                    + "ExecStart=" + " ".join(service_quote(arg) for arg in command)
                    + "\nRestart=on-failure\nRestartSec=2\nUMask=0077\n\n[Install]\nWantedBy=default.target\n")
            self._replace(self.unit, unit.encode())
            self._replace(self.marker, self.upstream.encode())
            run_service(self.systemctl, "daemon-reload")
            self.started = True
            run_service(self.systemctl, "enable", SERVICE)
            run_service(self.systemctl, "restart", SERVICE)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 15
            while True:
                try:
                    with opener.open(self.health_url, timeout=1) as response:
                        value = json.loads(response.read(4096))
                    if value.get("service") == SERVICE and value.get("instance_id") == self.instance_id:
                        break
                except (OSError, ValueError):
                    pass
                if time.monotonic() >= deadline:
                    raise ProxyInstallError("proxy health check failed; Codex connection was not switched")
                time.sleep(0.2)
            # Codex plugin registration may have updated this file since preflight.
            before_switch = self.config_path.read_bytes()
            current = tomllib.loads(before_switch.decode())
            original_provider = self.config["model_providers"][self.provider_name]
            if current.get("model_provider") != self.provider_name or current["model_providers"][self.provider_name] != original_provider:
                raise ProxyInstallError("provider changed during installation; rerun using the updated configuration")
            replacement = set_provider_url(before_switch.decode(), self.provider_name, self.local_url)
            backup = backup_root / f"codex-config.{time.time_ns()}.toml"
            write_atomic(backup, before_switch)
            self._replace(self.config_path, replacement.encode())
            self._replace(self.metadata, json.dumps({"provider": self.provider_name, "upstream": self.upstream,
                "local_url": self.local_url, "config_backup": str(backup), "service": str(self.unit)}, indent=2).encode())
            return {"upstream": self.upstream, "proxy_port": self.port, "service": str(self.unit), "config_backup": str(backup)}
        except BaseException as exc:
            recovery = []
            if self.started:
                try:
                    run_service(self.systemctl, "stop", SERVICE, check=False)
                except ProxyInstallError as error:
                    recovery.append(str(error))
            for path, previous in reversed(list(self.saved.items())):
                try:
                    if previous is None:
                        path.unlink(missing_ok=True)
                    else:
                        write_atomic(path, previous)
                except OSError:
                    recovery.append(f"restore needed for {path}")
            if self.started:
                try:
                    run_service(self.systemctl, "daemon-reload")
                    run_service(self.systemctl, "enable" if self.was_enabled else "disable", SERVICE, check=False)
                    if self.was_active:
                        run_service(self.systemctl, "restart", SERVICE)
                except ProxyInstallError as error:
                    recovery.append(str(error))
            details = ("; recovery: " + "; ".join(recovery)) if recovery else "; managed files restored"
            raise ProxyInstallError(f"proxy installation failed ({type(exc).__name__}: {exc}){details}") from exc
