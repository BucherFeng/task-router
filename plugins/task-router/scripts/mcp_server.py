#!/usr/bin/env python3
"""MCP entry: ordinary users submit tasks through Codex conversation."""

import asyncio
import os
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, StrictBool, StrictInt

from task_router_runtime.service import Service, public_report

server = FastMCP("task-router", log_level="WARNING", instructions="Use task_submit for authorized tasks, then task_wait/task_status for results. Submitted work is executed in a background process. Do not duplicate a submitted task by editing its files in the foreground.")
service = Service()


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True))
async def task_submit(
    task_type: Annotated[str, Field(min_length=1, max_length=100)],
    prompt: Annotated[str, Field(min_length=1, max_length=100000)],
    cwd: Annotated[str, Field(min_length=1)],
    request_key: Annotated[str, Field(min_length=1, max_length=200)],
    allow_write: StrictBool = False,
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 300,
) -> dict[str, Any]:
    """Submit and START an authorized task in the background. Classify using the user's intent and pass an absolute project cwd. Editing requires allow_write=true and prior user intent to edit. Include constraints and accepted decisions. Use a stable unique request_key for this user request, reusing it only on duplicate submission. Do not submit secrets. Returns task_id; collect it with task_wait."""
    return await asyncio.to_thread(service.submit, task_type=task_type, prompt=prompt, cwd=cwd,
                                   request_key=request_key, allow_write=allow_write, timeout_seconds=timeout_seconds)


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def task_status(task_id: str | None = None, result_offset: Annotated[StrictInt, Field(ge=0)] = 0) -> dict[str, Any]:
    """Get a task's durable status and result, or list recent tasks when task_id is absent."""
    return public_report(await asyncio.to_thread(service.status, task_id), result_offset)


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def task_wait(task_id: str, wait_seconds: Annotated[StrictInt, Field(ge=0, le=20)] = 10) -> dict[str, Any]:
    """Wait up to 20 seconds for completion and return current status. Repeat while running; report results only after a terminal state. This does not cancel background work."""
    return public_report(await asyncio.to_thread(service.wait, task_id, wait_seconds))


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
async def task_cancel(task_id: str) -> dict[str, Any]:
    """Request cancellation; check task_wait afterwards. A request is not proof that the worker has stopped."""
    return public_report(await asyncio.to_thread(service.cancel, task_id))


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True))
async def task_resume(task_id: str) -> dict[str, Any]:
    """Resume a saved queued/retrying task. Unknown execution is never blindly replayed; completed tasks are returned without running again."""
    return public_report(await asyncio.to_thread(service.resume, task_id))


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def router_diagnose() -> dict[str, Any]:
    """Inspect policy, configured models and runtime prerequisites without calling a model. Does not prove quota or connectivity."""
    return await asyncio.to_thread(service.diagnose)


if __name__ == "__main__":
    if os.environ.get("TASK_ROUTER_WORKER") == "1":
        for name in ("task_submit", "task_status", "task_wait", "task_cancel", "task_resume", "router_diagnose"):
            server.remove_tool(name)
    server.run(transport="stdio")
