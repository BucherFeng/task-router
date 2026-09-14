---
name: task-router
description: Manage discussion, planning, code reading, edits and review through Task Router's conversation tools. Submit work and collect results without asking the user to run terminal commands. Assigned workers execute their task directly.
---

# Task Router

Use the plugin's MCP tools for substantive work. The host interprets the user's
request and hands it to the background controller; the controller selects models,
persists progress and manages bounded retries. Users interact through Codex
conversation, without entering scripts or task-type flags in a terminal.

## Worker guard

When assigned execution_role: worker, do the assigned work directly. Do not submit
it again, call this plugin's broker, or delegate further. Managed workers have the
broker disabled through their actual process environment as well.

## Submit work from the conversation

Classify the user's intent into an existing task type:

- discussion: ordinary questions and approach discussion.
- planning / deep-analysis: design, difficult reasoning.
- read-code: locate or explain code without edits.
- edit / small-edit / bulk-rewrite: requested code modifications.
- test: requested tests or verification.
- review: examine actual code or diffs; no changes unless requested separately.

Use task_submit with the task type, absolute project directory, the user's goal
and constraints, and a unique request_key for this user request. Reuse that key
when retrying an uncertain submission. Set allow_write only for authorized edits;
discussion and review remain read-only. Choose a bounded timeout suitable for
the task. Do not ask the user to supply internal task types, IDs or commands.

Use the most specific relevant project directory from the conversation or
workspace context. For edits, avoid broad roots containing the controller's own
state. Read only enough to locate the work and provide the worker with necessary
context, accepted decisions, existing changes and acceptance criteria.

The plugin tools may have a namespace prefix; use the task_submit, task_wait,
task_status, task_cancel, task_resume and router_diagnose tools actually exposed
by the current environment. Do not invent tool names or substitute a terminal
instruction for a missing tool. If tools are not loaded, explain that the plugin
must be enabled and picked up in a new conversation, and diagnose the installed
plugin when tooling permits.

Short acknowledgments with no pending work can stay inline. "Continue" refers to
the existing task and its constraints; it is not automatically a trivial reply.
For a mixed discussion-and-edit request, submit the parts in dependency order.
A request to discuss implementation does not authorize implementation.

## Collect and present the result

task_submit returns an ID immediately; it does not mean the work is complete.
Keep the ID in the thread and call task_wait (up to 20 seconds per call) until
completion or a meaningful state requiring attention. Give concise progress
updates during longer tasks. Do not edit the same files in the foreground.

For a long result, task_status accepts result_offset; follow next_result_offset
until the needed result is collected. Report the actual outcome and remaining
issues. completion_verified is false unless a separate verifier ran; the MCP
entry currently relies on worker-reported test evidence and host review. Do not
claim programmatic verification solely because the task status is succeeded.

The user can simply say "show progress", "cancel that task", or "continue".
Translate these into task_status/task_wait, task_cancel, and task_resume. Query
recent tasks when the context lacks an ID; clarify only if the choice is ambiguous.
Cancellation is a request, so collect the final state before claiming it stopped.
Successful tasks are returned from saved state rather than executed again.

## Failure and configuration

The background controller retries only confirmed terminal availability failures
within its stored budget and configured candidate pool. It does not replay unknown
execution, reset exhausted budgets or treat 503 as proof of exhausted quota.

For unknown, failed, permission-required or blocked tasks, inspect the returned
reason and explain what needs attention. Never start a duplicate writer to bypass
a blocked task. An interrupted worker may already have changed files.

Use router_diagnose for configuration and model-selection issues. Model IDs in a
catalog do not prove access or quota. Authentication errors require diagnosis,
not repeated calls to every model. Read
[routing-schema.md](references/routing-schema.md) for policy changes.

The plugin includes its runtime and starts its tools through Codex. First startup
may automatically prepare a private MCP SDK environment if the SDK is missing.
It does not install dependencies into the user's global Python environment.

The host must still submit the task. If the host model fails before submission,
the plugin cannot recover that unseen request. Work already submitted continues
independently of the foreground MCP connection; do not promise transparent
recovery of every existing Codex thread.
