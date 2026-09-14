#!/usr/bin/env python3
"""Compatibility entry for developer CLI use; ordinary users use plugin tools."""

from task_router_runtime.cli import main, parser, task_payload

if __name__ == "__main__":
    raise SystemExit(main())
