#!/usr/bin/env python3
"""Internal task runner entry, also available for developer diagnostics."""

from task_router_runtime.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
