"""Compatibility imports for the runtime now distributed inside the plugin."""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[2] / "plugins/task-router/scripts/task_router_runtime")]
