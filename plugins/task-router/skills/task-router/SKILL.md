---
name: task-router
description: Classify tasks and route them to the right model using the routing.json policy. Use whenever a request involves code changes, long-context analysis, or a mix of discussion and edits.
---

# Task Router

Routing decisions come from `routing.json` in this skill directory. Never
hardcode model ids in your reasoning; resolve them with the helper script so
config changes take effect immediately.

The host thread is a dispatcher, not a participant: classify each request,
resolve the route, and delegate substantive work. The host model answers
trivial exchanges inline and absorbs delegated work itself only after a whole
failover chain is exhausted.

## Classify the task

Map the request to one of these types:

- `trivial`: short acknowledgments, yes/no replies, one-line follow-ups with
  no new substance.
- `discussion`: questions, explanations, clarification, architecture talk;
  routed to the GPT-family model.
- `planning`: designing an approach or breaking work into steps; GPT-family.
- `review`: examining code for issues with no edits requested; GPT-family.
- `small-edit`: mechanical change, roughly under 10 lines in 1-2 files.
- `edit`: concrete code modification (feature, bugfix, refactor, tests);
  GLM-family.
- `bulk-rewrite`: mechanical identical transformation across many files;
  GLM-family.
- `long-context`: analysis that requires reading large documents or a large
  portion of the codebase; GLM-family.
- `deep-analysis`: hard reasoning over a complex problem where model depth
  matters more than speed; GPT-family.

If the request mixes discussion with edits, resolve and route each part
separately: the discussion part through its own route, the edit part through
its own.

## Resolve the route

Run the resolver from a shell:

```bash
python3 <skill-dir>/scripts/route.py --task <type>
```

`<skill-dir>` is the directory containing this SKILL.md. The output is one
JSON line:

```json
{"task": "edit", "delegate": true, "model": "glm-5.3", "fallback": false}
```

- `delegate: true`: spawn a sub-agent with `model: "<model>"` (see below).
- `delegate: false`: handle the task in the current thread; `model` is
  informational and the thread keeps its own model. Inline handling applies to
  `trivial`, `small-edit`, and to any task whose failover chain was exhausted.
- `fallback: true`: the task type was unknown; the safe default is to stay in
  the current thread and note the unmatched type in your final answer.

To see the full policy and catalog, run with `--list` instead of a task type.

## Delegating

Before spawning, read the relevant code and scope the change precisely. Then:

1. Call `spawn_agent` with:
   - `fork_context: true` (the sub-agent inherits the codebase context)
   - `model: "<resolved model>"`
   - a task message containing:
     - the exact files to modify
     - what the change must accomplish and any constraints
     - how to verify (build/test/lint commands, expected outcome)
     - instruction to report changed file paths and a summary of edits
2. Call `wait_agent` to collect the result.
3. Review the reported diff or changed files yourself before considering the
   task done. Do not accept a delegation result unread. If the change is wrong
   or incomplete, send precise follow-up input to the same agent rather than
   spawning a new one.

## Failure handling

When a delegated agent fails, first classify the failure:

- **Availability failure** (the error mentions 503, 429, quota, insufficient
  credit, or model unavailability): run
  `python3 <skill-dir>/scripts/route.py --failover <failed-model>` and retry
  the delegation with the next model in the returned chain, skipping models
  that already failed in this conversation. If the chain is exhausted, stop
  delegating that task and complete it directly on the primary model instead,
  noting in your final answer that the task was not delegated.
- **Task failure** (the agent ran but the result was wrong, incomplete, or it
  timed out without a quota error): send precise follow-up input to the same
  agent. Only after two unusable results, complete the task directly on the
  primary model instead, noting it in your final answer.

Do not retry a model that returned an availability failure earlier in this
conversation unless the user says the quota was refreshed.

## Extending the policy

To add models or change which model handles which task type, edit
`routing.json` and read `references/routing-schema.md` for the exact field
semantics and a worked example. No changes to this file are needed.
