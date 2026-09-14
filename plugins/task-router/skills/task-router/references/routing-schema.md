# Routing configuration v2

Python 3.11+ is required. The resolver uses only the standard library.

## Configuration source and editing

One complete file is loaded, without merging:
--config PATH > TASK_ROUTER_CONFIG > $XDG_CONFIG_HOME/task-router/routing.json
(default ~/.config/task-router/routing.json) > the bundled routing.json.
An invalid explicit file fails; it never falls back to different rules.

Every result includes config_path, config_source and config_hash. User config
changes are read on the next invocation. In-flight tasks should retain their
original policy and hash. Changes to packaged skills require reinstall and a
new thread; editing source files does not update the installed cache.

## Required schema

The top-level fields are schema_version (integer 2), dispatcher, profiles, roles,
tasks, fallback and max_attempts (integer 1..10). Unknown fields, duplicate JSON
keys, missing references and wrong types are errors.

- dispatcher: {"model_id": "glm-5.3-flash"}. This is an expected host model,
  not a command to switch it. Codex defaults and actual thread state may differ.
- profiles: stable names mapped to objects requiring model_id, enabled (boolean)
  and reasoning_effort. Change model_id here to replace a model without rewriting
  every role. "default" effort resolves from the local model catalog; if that
  default is unknown, the candidate is rejected. No inherited host effort.
- Optional profile fields: provider (must match configured host provider),
  context_window (positive integer), capabilities (unique string array).
  These are explicit user assertions; do not invent capabilities or limits.
- roles: role names mapped to nonempty ordered arrays of profile names.
  Unknown/repeated profile references are rejected. Repeated actual model IDs
  are considered only once among eligible candidates; aliases with different
  IDs are not assumed to have independent quotas.
- tasks: task names mapped to exactly {"role": "role-name"} or {"inline": true}.
- fallback: the same shape, used for unknown task types.
- max_attempts: total per-task model attempts including quality corrections.
  The host supplies --attempt and tracks the count; no persistent counter exists.

Bundled role preferences are GPT for discussion/reasoning/review and GLM for
code tasks. These are configurable preferences, not benchmark rankings.
Profiles are candidates; enabled does not imply tested or available.

## Selection and diagnostics

~~~bash
python3 <skill-dir>/scripts/route.py --validate
python3 <skill-dir>/scripts/route.py --list
python3 <skill-dir>/scripts/route.py --doctor
python3 <skill-dir>/scripts/route.py --task edit --explain
python3 <skill-dir>/scripts/route.py --task edit --input-tokens 20000 --output-tokens 4000
~~~

The catalog defaults to model_catalog_json in the user's Codex config. Override
with --catalog or inspect a different Codex file with --codex-config.
Missing or malformed explicitly configured catalogs fail; no API key is read or
printed by the resolver. Codex provider switching is not implemented.

Candidates are filtered by enablement, prior exclusions, provider, catalog
membership, runtime support, reasoning effort, requested capabilities and input
plus reserved output size, then selected in order. Where multiple limits exist,
the most conservative context limit is used. Declared context sizes are not live
provider guarantees. No requested token estimate means no length check.

--require tools rejects unknown tool capability, even if the model appears in
the catalog. --require-verified requires explicit runtime delegation evidence.
Without runtime evidence, ordinary selection returns warnings for the host to
resolve; it is not proof that spawning will succeed.

Exit codes: 0 success/inline or diagnostic report; 2 invalid input/configuration;
3 no compatible candidate or exhausted attempt budget. --doctor's status can
be attention with exit 0: inspect notes and routes rather than just exit code.
--list now outputs JSON. --failover requires --for-task because candidate pools
belong to roles, and returns the next choice and the remaining eligible chain.

## Runtime evidence

The shell cannot introspect the agent's current tool definitions. A host may
supply a snapshot through --runtime PATH only when it describes this session.
It expires after one hour; refresh it when runtime/provider/session changes.
It is never loaded implicitly or included in a shared plugin package.

~~~json
{
  "schema_version": 1,
  "observed_at": "2026-09-14T12:00:00+00:00",
  "source": "current-session tool declaration and bounded task result",
  "complete": false,
  "models": {
    "glm-5.3": {
      "delegation": true,
      "reasoning_efforts": ["low"],
      "capabilities": ["text", "tools"]
    }
  }
}
~~~

Replace the timestamp with the actual observation time. delegation is mandatory;
reasoning_efforts, capabilities and positive context_window are optional.
Do not infer all capabilities from one reply or the model's claimed identity.
complete=false leaves unlisted models unknown; only an exhaustive source
justifies complete=true. Explicit false excludes a model. A limited list in a
tool description alone does not prove all other overrides are unsupported.

## Migration and initialization

~~~bash
python3 <skill-dir>/scripts/route.py --init-config
python3 <skill-dir>/scripts/route.py --migrate --config old.json --output migrated.json
~~~

Both create new files exclusively and refuse overwrites. The installer backs
up existing v1 user config before replacing it; direct --migrate leaves its
input untouched. Valid v1 can also be read in memory with a migration note.

Migration preserves existing route choices (including inline small-edit), model
IDs and direct fallback order. Each old task gets its own legacy role. Mutual
v1 chains are flattened per task, never recursively expanded. Missing targets,
self/duplicate fallback entries and unknown references are errors.

Advisory v1 family/tier/strengths/context labels and unused cooldown/status-code
fields are not imported as verified capabilities or functioning mechanisms.
To adopt the new default task grouping, create defaults at a new path and
review them before replacing personal policy. v0.4 does not implement persistent
cooldown, quota polling, automatic retries, or host-model failover.
