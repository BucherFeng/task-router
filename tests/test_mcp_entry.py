import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/task-router"
sys.path.insert(0, str(ROOT / "scripts"))
from task_router_runtime.store import Store

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


@unittest.skipUnless(HAS_MCP, "MCP SDK required for conversation-entry tests")
class MCPEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        self.state = self.root / "state"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        shutil.copy2(ROOT / "tests/fixtures/fake_app_server.py", self.bin / "codex")
        policy = {"schema_version": 2, "dispatcher": {"model_id": "first"},
                  "profiles": {"first": {"model_id": "first", "enabled": True, "reasoning_effort": "low"},
                               "second": {"model_id": "second", "enabled": True, "reasoning_effort": "high"}},
                  "roles": {"coding": ["first", "second"]},
                  "tasks": {"edit": {"role": "coding"}, "read-code": {"role": "coding"}},
                  "fallback": {"inline": True}, "max_attempts": 2}
        self.config = self.root / "policy.json"
        self.config.write_text(json.dumps(policy))
        self.environment = {**os.environ, "TASK_ROUTER_CONFIG": str(self.config), "TASK_ROUTER_STATE_DIR": str(self.state),
                            "TASK_ROUTER_FAKE_SCENARIO": "success", "PYTHONDONTWRITEBYTECODE": "1",
                            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")}
        self.environment.pop("TASK_ROUTER_WORKER", None)
        self.server_script = PLUGIN / "scripts/mcp_server.py"

    @asynccontextmanager
    async def connect(self):
        # Isolate model declarations from the machine's user catalog; workers use only the fake CLI.
        setup = "import sys,runpy;sys.path.insert(0,sys.argv[1]);import task_router_runtime.cli as c;c.host_settings=lambda:({},None);runpy.run_path(sys.argv[2],run_name='__main__')"
        parameters = StdioServerParameters(command=sys.executable,
            args=["-B", "-c", setup, str(self.server_script.parent), str(self.server_script)], env=self.environment)
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session

    def args(self, key="task-one", **updates):
        value = {"task_type": "edit", "prompt": "Complete the local fixture", "cwd": str(self.workspace),
                 "request_key": key, "allow_write": True, "timeout_seconds": 20}
        value.update(updates)
        return value

    async def complete(self, session, task_id):
        for _ in range(8):
            result = await session.call_tool("task_wait", {"task_id": task_id, "wait_seconds": 1})
            self.assertFalse(result.isError, result.content)
            output = result.structuredContent
            if output["status"] in {"succeeded", "failed", "cancelled", "unknown"}:
                return output
        self.fail("task did not finish")

    async def test_tools_submit_wait_and_duplicate_submission(self):
        async with self.connect() as session:
            tools = await session.list_tools()
            self.assertEqual({tool.name for tool in tools.tools},
                             {"task_submit", "task_status", "task_wait", "task_cancel", "task_resume", "router_diagnose"})
            first = await session.call_tool("task_submit", self.args())
            self.assertFalse(first.isError, first.content)
            task_id = first.structuredContent["task_id"]
            duplicate = await session.call_tool("task_submit", self.args())
            self.assertEqual(duplicate.structuredContent["task_id"], task_id)
            result = await self.complete(session, task_id)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(len(result["attempts"]), 1)
            self.assertFalse(result["completion_verified"])

    async def test_worker_finishes_after_mcp_frontend_disconnects(self):
        async with self.connect() as session:
            submitted = await session.call_tool("task_submit", self.args())
            task_id = submitted.structuredContent["task_id"]
        for _ in range(50):
            store = Store(self.state)
            try:
                status = store.task(task_id)["status"]
            finally:
                store.close()
            if status in {"succeeded", "failed", "unknown"}:
                break
            await asyncio.sleep(0.1)
        self.assertEqual(status, "succeeded")
        async with self.connect() as session:
            result = await session.call_tool("task_status", {"task_id": task_id})
            self.assertEqual(result.structuredContent["status"], "succeeded")

    async def test_tool_cancel_stops_active_worker(self):
        self.environment["TASK_ROUTER_FAKE_SCENARIO"] = "timeout"
        async with self.connect() as session:
            submitted = await session.call_tool("task_submit", self.args())
            task_id = submitted.structuredContent["task_id"]
            await asyncio.sleep(0.5)
            cancelled = await session.call_tool("task_cancel", {"task_id": task_id})
            self.assertFalse(cancelled.isError)
            self.assertEqual((await self.complete(session, task_id))["status"], "cancelled")

    async def test_resume_completed_does_not_execute_again(self):
        async with self.connect() as session:
            submitted = await session.call_tool("task_submit", self.args())
            task_id = submitted.structuredContent["task_id"]
            await self.complete(session, task_id)
            resumed = await session.call_tool("task_resume", {"task_id": task_id})
            self.assertEqual(len(resumed.structuredContent["attempts"]), 1)

    async def test_strict_permission_and_timeout_types(self):
        async with self.connect() as session:
            for changes in ({"allow_write": "true"}, {"timeout_seconds": True}, {"allow_write": False}):
                with self.subTest(changes=changes):
                    result = await session.call_tool("task_submit", self.args(**changes))
                    self.assertTrue(result.isError)

    async def test_managed_worker_has_no_broker_tools(self):
        self.environment["TASK_ROUTER_WORKER"] = "1"
        async with self.connect() as session:
            tools = await session.list_tools()
            self.assertEqual(tools.tools, [])

    async def test_copied_plugin_runs_without_repository_scripts(self):
        copied = self.root / "standalone-plugin"
        shutil.copytree(PLUGIN, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.server_script = copied / "scripts/mcp_server.py"
        async with self.connect() as session:
            submitted = await session.call_tool("task_submit", self.args())
            self.assertFalse(submitted.isError, submitted.content)
            result = await self.complete(session, submitted.structuredContent["task_id"])
            self.assertEqual(result["status"], "succeeded")

    async def test_partial_failure_is_recovered_by_background_controller(self):
        self.environment["TASK_ROUTER_FAKE_SCENARIO"] = "partial"
        async with self.connect() as session:
            submitted = await session.call_tool("task_submit", self.args())
            result = await self.complete(session, submitted.structuredContent["task_id"])
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual([a["model"] for a in result["attempts"]], ["first", "second"])
            self.assertEqual((self.workspace / "progress.txt").read_text(), "step one\nstep two\n")


if __name__ == "__main__":
    unittest.main()
