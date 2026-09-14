---
name: task-router
description: Route substantive discussion, planning, code reading, editing and review through configurable model pools. Use for these tasks and mixed requests; delegated workers execute their assigned work without routing again.
---

# Task Router

The host coordinates work; the selected workers perform the substantive task.
User instructions and existing task authorization take precedence over routing.
This skill guides the host; the Python helper does not spawn agents or intercept
API calls. Runtime tools, model availability and file-sharing behavior must be
checked in the current environment.

## Worker guard

If you received an assigned worker task (execution_role: worker), execute it
directly. Do not invoke this router to delegate it again. Report the actual
result and evidence to the parent. This is a workflow convention; do not claim
the plugin has disabled tools or enforced a sandbox.

## Classify and resolve

Use these task names as defaults; the active config may supply additional tasks:

- trivial: acknowledgments with no pending work. "Continue" resumes existing
  work and its constraints; it is not automatically trivial.
- discussion: substantive questions and ordinary approach discussion.
- planning / deep-analysis: complex design and reasoning.
- read-code: locate or explain code; no edits.
- edit / small-edit / bulk-rewrite: authorized code modifications.
- test: run or maintain tests within the requested scope.
- review: review actual code or diffs; no edits unless requested.
- long-context: retained for compatibility. Prefer the underlying task plus an
  input/output token estimate when selecting a model.

Separate discussion and editing when a request includes both, and preserve
their dependencies. A request to discuss an implementation does not authorize
the implementation. For continuing work, reuse the task state and decisions.

Run the helper at the absolute path relative to this installed SKILL.md:

~~~bash
python3 <skill-dir>/scripts/route.py --task edit --explain
~~~

The helper reads an explicit --config, TASK_ROUTER_CONFIG, the external user
config, or bundled defaults, in that order. Do not edit the installed cache.
Use --doctor to inspect the actual config source, model catalog and limitations.
Read [routing-schema.md](references/routing-schema.md) when changing policy,
creating a runtime snapshot, or interpreting compatibility diagnostics.

The result includes status, delegate, model, reasoning_effort, role, profile,
config_hash, candidates and warnings. Inline status means execute within the
already authorized scope. Unavailable status (exit 3) means stop selecting and
explain the unavailable candidates; do not silently inherit the host model.
Configuration errors (exit 2) should be corrected, not retried as quota failures.

The model catalog is a declaration, not a live availability test. Check whether
the selected override and reasoning effort are supported by the tools now
available. If support is uncertain, report uncertainty and use at most a
bounded, authorized probe; an incomplete tool description is not an exhaustive
deny list. Do not invent model IDs or treat a model's self-description as proof.

For known tool/context requirements, pass --require tools and --input-tokens /
--output-tokens. These are conservative checks of supplied evidence, not exact
token counting. A fresh --runtime snapshot may carry current-session evidence;
--require-verified rejects targets without confirmed delegation support.
Without a snapshot, warnings about unverified delegation require host checking.

## Dispatch

Read only enough to locate the work and pass a useful task package. Delegate
large code exploration instead of doing it twice. Before using concurrent edits,
verify the runtime's actual workspace behavior; start with one writing worker.

Supply the following to a worker:

- execution_role: worker, task identifier, and instruction not to delegate again.
- User goal, accepted decisions, constraints, relevant source references.
- Read-only or editing scope, existing changes, and acceptance conditions.
- Request for conclusions with evidence; for edits, changed files and actual
  test results, with omissions or remaining issues reported explicitly.

Call spawn_agent with the resolved model AND reasoning_effort explicitly. Use
the field names and wait/input/close tools actually available in this session.
Prefer a focused task message. Fork the complete history only when needed and
when the target can accommodate it, including tools and output space.

Reuse a live worker for related follow-ups when supported. If it is no longer
available, pass a compact record of decisions and completed work to a new one.
Inspect returned artifacts and evidence before reporting completion. Close
finished workers after collecting the required results.

## Failure and retry

v0.4 has no persistent cooldown database or automatic retry executor. The host
must track attempted models and total attempts for each task; the helper can
only enforce the attempt number and exclusions that the host supplies.

Classify errors from actual tool results, not incidental mentions of "503" or
"quota" in user text or repository files. A 503 alone does not prove exhausted
quota. Configuration/authentication errors need diagnosis; tool task quality
errors need focused correction, not a service-health penalty.

For a terminal availability failure, use the task's pool to select a replacement:

~~~bash
python3 <skill-dir>/scripts/route.py --failover <failed-model> --for-task edit \
  --attempt 2 --exclude-model <earlier-failed-model> --explain
~~~

Pass all prior exclusions and the same config for this task. Count quality
corrections against max_attempts too. Do not reset the budget when switching
models. Report exhaustion rather than silently taking over all edits.

A wait timeout does not prove the worker has stopped. Query or stop it before
starting a replacement writing worker. If termination cannot be confirmed,
report the task as uncertain and avoid a second writer. Check partial changes
before retrying, and send a continuation task rather than replaying completed
steps. Never replay external side effects just because the model changed.

If the host model itself fails, the plugin cannot execute this recovery logic.
