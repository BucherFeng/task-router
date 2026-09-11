# routing.json schema

`routing.json` has four top-level keys.

## `defaults`

- `primary` (string): the model id the host thread is expected to run. Only
  used to resolve the `"primary"` alias inside routes. Changing this does not
  change the host's actual model; that remains controlled by `/model` or
  `config.toml`.

## `models`

Catalog of every model the routes may target. Keyed by exact model id.

- `family` (string): free-form grouping, e.g. `gpt`, `glm`.
- `tier` (string): free-form quality/speed label, e.g. `fast`, `flagship`.
- `strengths` (array of strings): what this model is good at. Used by humans
  when editing routes; not parsed by the resolver.
- `context_window` (string): advisory label, e.g. `200k`, `1m`.

A model must appear here before any route can reference it; the resolver
exits with an error on unknown ids so config typos fail loudly.

## `routes`

Maps task types to routing decisions. Keyed by task type (must match the
classification list in SKILL.md to be picked up automatically).

- `delegate` (bool): `true` means spawn a sub-agent on `model`; `false` means
  handle the task in the current thread.
- `model` (string, optional): target model id or the alias `"primary"`.
  Omit it (or leave it out for `delegate: false` routes) when staying inline.

## `fallback`

The decision used when a task type is not found in `routes`. Same shape as a
route entry. Keep it non-delegating unless you specifically want unknown
tasks to be offloaded.

## `failover`

Availability-failure policy for delegated models. Not used by `resolve`;
read by the `--failover` lookup.

- `on_status` (array of ints): HTTP status codes that count as availability
  failures, e.g. `429`, `502`, `503`.
- `cooldown_seconds` (int): advisory reminder of how long to avoid a model
  after it returned an availability failure. State lives in the conversation,
  not on disk; the skill instructions enforce this by not retrying a failed
  model within the same thread.
- `chains` (object): maps each model id to an ordered list of fallback ids.
  The `--failover` lookup filters entries to ids that exist in the `models`
  catalog, so stale chain entries are ignored rather than causing errors.

Keep chains short and prefer fallbacks whose `strengths` cover the same task
types as the primary target.

## Worked example: adding a GPT model

Suppose your catalog gains `gpt-6`, a strong reasoner, and you want deep
analysis to use it while edits stay on GLM:

```json
{
  "models": {
    "gpt-6": {
      "family": "gpt",
      "tier": "flagship",
      "strengths": ["reasoning", "planning", "coding"],
      "context_window": "1m"
    }
  },
  "routes": {
    "deep-analysis": { "delegate": true, "model": "gpt-6" }
  }
}
```

Add the `gpt-6` entry inside the existing `models` object and the
`deep-analysis` entry inside the existing `routes` object (the snippet above
shows only the pieces being added). Verify with:

```bash
python3 <skill-dir>/scripts/route.py --task deep-analysis
```

No other files need to change.
