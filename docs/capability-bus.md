# Capability Bus (0.6.3)

## 1. Why a bus, not an orchestrator

The Phase 2 conclusion: generalized multi-agent orchestration is not worth it in time, tokens or reliability; models will keep getting stronger,
and upstream hosts will keep absorbing subagents / skills / plugins / MCP. CapRouter's differentiation should not be
"another general agent", but rather:

1. **Local capability map**: what skills/plugins/projects/CLI agents/MCP servers actually exist on this machine;
2. **Capability routing**: select capabilities by "what it has done" rather than "what its name looks like", and abstain when uncertain;
3. **Acceptance evidence**: deterministic checks such as character count, item count, format, JSON, claim ledger — who executed them and how they were verified;
4. **Capability evidence**: probes turn "installable, findable, invocable" into reviewable pass/fail/latency.

The host (Claude Code / Cherry / other MCP clients) owns the main loop; CapRouter only provides
`search / get / invoke / verify / probe / outcome / gaps`.

## 2. Pipeline

`plan (host) -> select (bus) -> execute (host/worker) -> verify (bus) -> repair/fallback`

| Stage | Who | Description |
|---|---|---|
| Plan | Host agent | Split subtasks, write input/output contracts and acceptance criteria |
| Select | CapRouter | Two-stage recall + reranking + abstain |
| Execute | Host/worker | prompt mode reads the skill; command mode executes per Card permissions |
| Verify | CapRouter | Deterministic checks; LLM semantic checking is optional, not the default |
| Repair/Fallback | Host | At most 2 reworks; write a gap or ask the user when a capability is missing |
| Probe | CapRouter + host | Static checks run automatically; behavioral tasks are executed by the host and backfilled |

## 3. Capability Card

Registry entry + overlay normalized into one Card:

- Identity: `id/type/name/description/path/version/hash`
- Capabilities: `capabilities/domains`, `input_schema/output_schema`
- Evidence: `examples`, `probe` (status/evidence/pass_rate/checks/failures)
- Execution: `invoke.mode` (prompt/command/none), `invoke.allow`, `command`, `permissions`
- Results: `stats` (calls/success_rate/avg_duration_ms/avg_human_edit_minutes)
- Source: `provenance` (source/version/hash)

Overlay path: `<workspace>/.caprouter/capability_cards.json`; for an example see
`examples/capability_cards.example.json`. Without an overlay, skills/plugins default to
`prompt` mode and cli-agents default to `none` (no execution).

## 4. Probe evidence

Probes are two-level, and ranking must distinguish them:

| Evidence | Source | Meaning | Search bonus |
|---|---|---|---|
| `static` | Built-in static checks | Path, SKILL.md lint, plugin manifest, invoke content, CLI availability | Very small (+0.01) |
| `behavioral` | Host backfill / command probe | Really executed a task and got a decidable result | Full (+0.05) |
| `mixed` | Static + behavioral | Both present | Full (+0.05) |
| `fail` | Any required check failed | Capability broken or behavior failed | Downweighted (-0.15) |
| `pending` / `unknown` | Pending host task / no evidence | Unproven | No bonus |

Built-in static checks:

- `path_exists`: the capability path exists;
- `skill_lint`: name/description in the `SKILL.md` frontmatter are valid;
- `plugin_manifest`: `.claude-plugin/plugin.json` or `plugin.json` is valid and has a name;
- `invoke_content`: the `content_path` of prompt mode exists;
- `cli_available`: the CLI agent is on PATH;
- `hash_match`: compares the hash stored in the registry, only with `--hash`.

Overlay definition:

```json
{
  "probe": {
    "min_pass_rate": 0.8,
    "checks": [
      {"id": "host-1", "type": "host", "task": "convert page 3 of the example PDF to PNG"},
      {"id": "cmd-1", "type": "command", "command": ["python", "tool.py"], "allow": true,
       "acceptance": [{"type": "contains", "value": "ok"}]},
      {"id": "len-1", "type": "char_range", "min": 50, "max": 500, "unit": "han", "path": "README.md"}
    ]
  }
}
```

Rules:

- `host` checks only return pending; after the host executes, call `capability_probe_record` (MCP) or
  `capability probe-record` (CLI) to backfill pass/fail/latency.
- `command` checks must satisfy `check.allow=true`, config `bus.allow_execute=true`,
  and caller `allow_execute=true` at the same time; argv list, no shell.
- Other checks source from `path` / `text` / the capability body, then go through the deterministic checks of `capability_verify`.
- Probe history is appended (append-only) to `.caprouter/probes.jsonl`; the overlay is only an initial seed.
- Summary status: last fail → fail; when behavioral records exist, compute the pass rate over the latest 10 behavioral records
  (a static pass cannot wash out a behavioral fail); if the last behavioral result is pass and the pass rate ≥ `min_pass_rate` → pass;
  if there are other pendings → pending; otherwise unknown. A manual seed is not erased by purely static history.

## 5. Two-stage search

1. **Recall**: BM25 in `route.py` + Chinese/English alias expansion, take the candidate pool (at least 20).
2. **Rerank**: on top of the lexical score add
   - capability tag matching (query intent ∩ card capabilities);
   - example overlap (retrieve by task examples, not by name);
   - probe evidence (behavioral/mixed pass adds full points; static pass adds only a very small score; fail is downweighted);
   - historical success rate;
   - negative description penalty ("reading only / not supported" is downweighted for creation/conversion tasks);
   - unavailable/no-invoke capabilities are downweighted.
3. **abstain**: when top-1 confidence, the top-1/top-2 margin, or query coverage is insufficient, return
   `decision=abstain` rather than forcing a choice.

Default thresholds: `min_confidence=0.18`, `min_margin=0.02`, `min_coverage=0.12`,
overridable in the config `bus` block or the overlay `defaults`.

## 6. Acceptance gates

No LLM is called by default. Supports:

- `file_exists` / `non_empty`
- `char_range` / `min_chars` / `max_chars` (by Chinese characters or characters)
- `min_items` / `max_items` / `exact_items` (list/url/table/paragraph/source_ref)
- `format` (json / markdown / markdown_table)
- `json_required`
- `claim_ledger` (reuses the snapshot + hash + quote checks of `caprouter verify --claims`)
- `contains` / `not_contains` / `regex` / `sha256`

Free-text criteria try to parse common patterns; anything that cannot be judged by machine returns `unverifiable`
and cannot be counted as pass. Verdict: any fail → fail; no fail but some unverifiable → unverifiable;
all pass → pass.

## 7. Card drafts

`capability draft` deterministically generates Card drafts from local content, without calling a model:

- Input: registry id or local directory;
- Output: `examples`, `probe.checks` (host tasks for the first 3 examples), `permissions`, `invoke`,
  `provenance`, `review.notes`;
- Drafts are written to `.caprouter/drafts/`; only `--apply` merges them into the overlay;
- By default only missing fields are filled; `--force` overwrites existing non-empty fields;
- When examples / capabilities are missing, it marks this explicitly in `review.notes` rather than pretending to be complete.

## 8. Disk scan and bootstrap

```text
init if missing -> scan roots -> dedupe -> merge registry -> batch draft -> static probe
```

- Default roots: non-system `DRIVE_FIXED` drives; `%SystemDrive%` is skipped; `--drive` / `--project-root`
  can add explicitly; network/optical/RAM drives are not scanned by default, removable drives need `--include-removable`;
- Skip: system/software/cache directories, hidden/system attribute directories, reparse points/junctions (not followed);
- Each root has an independent budget: `max_depth=4`, `max_dirs=50000`, `time_budget_seconds=30`,
  `max_projects=500`; exceeding it writes `truncated`, not silent truncation;
- Project boundary: after hitting a primary marker (pyproject/package.json/.git/...) or SKILL.md/plugin.json,
  record it and stop recursing down; collect only path/name/marker/mtime/has_readme;
- Full results: `.caprouter/projects_scan.json`; summary ≤4KB, containing only counts, per-drive/type statistics,
  the latest N projects and truncated;
- Dedup: real path + id; the existing registry takes precedence, and `duplicates` is recorded;
- Later `scan` continues to merge the saved scan results (projects whose path still exists);
- Batch drafts: `capability draft --all` writes `.caprouter/drafts/projects/`;
  unchanged ones are skipped, `--apply` must be explicitly triggered by a human; bootstrap never applies automatically;
- `description` / `capabilities` / `keywords` / `domain` in the overlay are merged at read time,
  and take part in search and capability filtering immediately after apply, without rebuilding the registry.

## 9. Local installation

```text
preview -> approve -> copy -> manifest -> refresh registry -> draft
```

- Offline by default; online content must first enter quarantine:
  `discover (read-only search of skills.sh) → fetch (writes to quarantine) → vendor (six-step breakdown + Card draft) → install --approve`;
- Only `--approve` copies; no script is executed during installation;
- Target: `<workspace>/.caprouter/installed/<type>/<name>/`;
- `manifest.json`: source/kind/path, version, hash, permissions, file_count,
  executables, symlinks, warnings, approved;
- ZIP: reject path traversal, absolute paths, symlinks, encrypted members, oversized files (5000 files / 200MB);
- After install, add the installed skills/plugins roots to config and refresh the registry;
- Automatically generate a Card draft, but do not apply automatically and do not enable command execution automatically;
- With `--force`, the old directory is moved to `.caprouter/installed/.trash/`;
- `uninstall --id ... --yes`: only targets inside `installed/` are allowed, move to `.trash`, remove from registry and overlay Card;
- `rollback --id ... --yes`: restore the most recently uninstalled directory and Card; if the target already exists, `--force` is needed.

## 10. MCP tools

| Tool | Description |
|---|---|
| `capability_search` | top candidates + confidence + abstain |
| `capability_get` | full Card |
| `capability_invoke` | prompt returns the content path; command executes and returns the transcript |
| `capability_verify` | deterministic acceptance |
| `capability_record_outcome` | record success/duration/human_edit_minutes |
| `capability_record_gap` | record a missing capability |
| `capability_list_gaps` | list gaps |
| `capability_probe` | run deterministic probes (single or all) |
| `capability_probe_record` | backfill behavioral evidence executed by the host |
| `capability_probe_report` | view probe history and summary |
| `capability_card_draft` | generate a Card draft (read-only) |
| `capability_discover` | read-only search of remote skills.sh candidates (requires `allow_network=true`; no download, no install; fetch/vendor/install remain CLI-only) |

Plugin root `.mcp.json`:

```json
{
  "mcpServers": {
    "caprouter": {
      "command": "python",
      "args": ["${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py", "mcp"]
    }
  }
}
```

## 11. bus mode and orchestrator mode

- `mode: bus` (default for new workspaces): the host owns the main loop; the old `PreToolUse` guard lets host tools through;
  CapRouter only intercepts commands per Card permissions at `capability_invoke`.
- `mode: orchestrator`: the old 0.2 behavior, where the guard restricts WebSearch/WebFetch/Task/Bash/Write,
  and `caprouter run` dispatches nodes. Old workspaces without a `mode` field are treated as orchestrator.
  This is a compatibility path and receives no new features.

## 11.1 Online discovery (quarantine)

- `discover --query ... --allow-network`: read-only skills.sh search API, returns candidates; writes `.caprouter/discovery.jsonl`;
- `fetch --candidate owner/repo/skill --allow-network`: downloads to `.caprouter/quarantine/<source>/<skill>/`;
  validates relative paths, file count (≤500), total size (≤20MB), does not install or execute; writes `provenance.json`;
  also reads `LICENSE`/`COPYING` to identify common licenses (MIT/Apache-2.0/GPL/BSD/MPL/CC-BY), unrecognized is written as `unknown`;
- `vendor --candidate ...`: reads the quarantined `SKILL.md`, and per the `skill-deconstruction` six steps generates
  `deconstruction.md` and `card_draft.json`; does not install;
- Installation still goes through `install --approve`; when `state` is disabled, discover/fetch/vendor are all rejected;
- `quarantine list` lists downloaded items; `quarantine clean --candidate ... --yes` or
  `quarantine clean --older-than-days N --yes` cleans them; by default it only previews, and nothing is deleted without `--yes`;
- Only the `skills.sh` domain is allowed; no self-built marketplace, no automatic install.

## 12. Metrics

- Routing: held-out top-1 / top-3, abstain rate, gap discovery rate
- Execution: invoke success rate, average duration, token
- Acceptance: pass / fail / unverifiable ratio, rework count
- Probes: static pass rate, behavioral pass rate, first real-task success rate, probe latency
- Delivery: **manual edit minutes per successful delivery** (the ultimate metric, not the number of agents)

## 13. What this version does not do

- UI / tray rework
- Online marketplace / automatic download of unknown plugins
- General DAG engine / more adapters
- Multi-agent crawling (fetching still uses a deterministic worker; multiple agents are only used for independent research branches)
- LLM rerank / LLM semantic verify as the default path (can be an optional layer later)
- Automatic execution of host probe tasks (the host owns the main loop)
- Self-built marketplace / automatic install (online discovery only does read-only search + quarantine; uninstall/rollback are already provided)

## 14. Relationship with VeriResearch

CapRouter is infrastructure: capability bus + acceptance gates. VeriResearch is a vertical product: verifiable research.
VeriResearch can be CapRouter's first customer, but the two are not tightly coupled; VeriResearch's
snapshot/claim ledger model can also, in turn, serve as an evidence source for `capability_verify`.

## 15. Card review and migration (0.6.1)

### Review

```text
evidence (read-only) -> deterministic review -> optional semantic -> risk-graded apply
```

- Evidence: `package.json` scripts/bin, `pyproject`/`Cargo` entrypoints, Gradle/Maven/CMake
  metadata, README commands; does not execute, does not write to the project directory, does not save full text.
- Deterministic checks: id/path, description, examples, capabilities, permissions/invoke,
  retrieval replay (query the registry with examples, requiring top-1/top-3 hits).
- Risk (meaning "is it safe to write the overlay", not capability runtime risk):
  - high: command invoke / `allow=true`, id/path mismatch, missing path;
  - medium: net/write/shell declarations **without evidence support**, insufficient examples, unknown tags, weak retrieval;
  - low: prompt/none invoke, evidence-backed permission declarations, examples/capabilities/retrieval up to standard.
- Automatic apply: low + score≥0.8; medium requires `review.allow_medium=true` and a semantic pass;
  high/rejected are never automatic.
- Semantic layer: built-in Card rubric, optionally appending the local review skill's checklist; returns strict JSON
  through a CLI adapter, only for re-checking, not as a gate. CodeBuddy/Qwen run in isolated mode with tools disabled;
  Kimi's isolation parameters are unverified, so it is rejected by default.
- `review --fix`: the semantic layer can return a validated `repaired` (description/examples/capabilities/keywords);
  after repair, a new deterministic review and an independent semantic re-check are run; permissions/invoke do not accept model rewrites.
- Semantic cache: `.caprouter/reviews/semantic-cache.json`, with the key containing the Card, evidence, rubric, adapter
  and prompt version; unchanged Cards are not re-called.

### Migration

```text
export: cards + identities + probes/outcomes/gaps
import: rescan -> match (id / hash / manifest / name / path hint) -> apply matched
```

- Absolute paths are not migrated; identity uses the manifest name, content-only metadata hash, and path hint.
- Successful match: reviewed Cards are written directly into the new overlay (no re-review); a project with the same id requires
  manifest/hash/path hint secondary confirmation, to avoid attaching the Card to the wrong project with a same-named directory.
- The bundle is external input: command/`allow=true` Cards are not applied automatically; write `migrated_unsafe` drafts.
- Unmatched: write to `drafts/migrated/`, do not silently drop.
- A new machine writes Cards only: `bootstrap --no-review`.

## 16. Post-task synthesis (0.6.2, refined in 0.6.3)

```text
search local -> search trusted sources -> gap -> task trace
  -> synthesize propose -> candidate (NOT registered)
  -> refine (builtin | skill-creator | adapter; optional, NOT registered)
  -> human review -> approve -> install + registry + Card + review
     or approve --auto -> install -> review real Card -> keep or roll back
  -> outcome -> improve -> versioned patch (NOT applied) -> approve --apply
```

### Trigger conditions

- There must be a `capability gap` record; without a gap, `synthesize propose` returns `no_gap`.
- Only enter synthesis when "neither the local registry nor trusted sources has a suitable capability"; ordinary reluctance to write it cannot be treated as a gap.
- Executed after the task ends; do not automatically write tools mid-task.

### Candidates and approval

- Candidate path: `.caprouter/synthesized/<slug>-<hash>/`
  - `skill/SKILL.md`
  - `skill/references/task-trace.md`
  - `skill/scripts/tool.py` (only with `--type script`)
  - `card_draft.json`
  - `candidate.json`
- Candidates do not enter the registry and are not executed; `synthesize approve` is an explicit manual approval.
- `approve` reuses the existing installer: copies to `.caprouter/installed/skills/`, refreshes the registry,
  generates a Card draft and runs the deterministic review; only `--apply` writes the reviewed Card into the overlay.

### Automatic approval (0.6.3)

```bash
caprouter synthesize approve --candidate <slug> --auto          # install/register only if it passes
caprouter synthesize approve --candidate <slug> --auto --apply  # apply the Card after passing
```

- Reuses `review_drafts`, adds no new approval rules.
- Installs first and generates a real Card draft; synthesized candidates prefer their own `card_draft.json`,
  only pointing `path` / `invoke.content_path` at the installed copy, to avoid heuristically re-inferring permissions.
- Only when review returns `auto_approved` are the installation and registry kept.
- On failure, delete the newly installed directory and draft, refresh the registry; if `--force` replaced an older version,
  restore it from `.trash`; the candidate stays `proposed`, with `auto_review` / `auto_rollback` written.
- `--auto` does not run semantic automatically and does not apply by default; writing the overlay requires an explicit `--apply`.
- The candidate Card is self-reported material; passing the review rules is not the same as task quality verification; probe and real outcomes are still needed.

### Content refinement (0.6.3)

```bash
caprouter synthesize refine --candidate <slug> --method auto
caprouter synthesize refine --candidate <slug> --method builtin
caprouter synthesize refine --candidate <slug> --method skill-creator --agent qwen
caprouter synthesize refine --candidate <slug> --method adapter --agent codebuddy
```

| method | Dependency | Behavior |
|---|---|---|
| `builtin` | none | Fills when/inputs/workflow/outputs/checks/failure/examples from the trace; always available |
| `skill-creator` | `skill:skill-creator` in the registry + CLI adapter | Uses skill-creator's SKILL.md as the writing specification, the adapter returns strict JSON, and rewrites SKILL.md and the Card description/examples |
| `adapter` | CLI adapter | Specify qwen / codebuddy / kimi directly |
| `auto` | optional | Prefer skill-creator, otherwise find an available adapter, otherwise fall back to builtin |

- Only `proposed` candidates are allowed; refinement does not change status, register or execute.
- The adapter call uses `disable_tools=True`, and the system prompt declares trace/guidance as untrusted data.
- If the adapter fails, times out, returns non-JSON or has invalid fields, the original candidate is kept and it falls back to builtin; warnings are written to
  `candidate.json`; refinement never puts the candidate into an unusable state.
- Before refinement, files are backed up to `refinements/<timestamp>/SKILL.md.before` and `card_draft.json.before`.
- The built-in `skills/capability-author/` provides the creation flow and checklist as the default creation tool for new users;
  it enforces "gap first, then candidate, then approval", and does not automatically execute any step.

### Continuous improvement

- `synthesize improve --id <capability_id>` reads `outcomes.jsonl` and generates:
  - `improvements/<timestamp>/patch.json` (version number, evidence, changes);
  - `improvements/<timestamp>/card_patch.json` (Card update suggestions extracted from outcome tasks/notes);
  - a `SKILL.md` candidate with a bumped version number.
- By default it does not replace the installed skill; `--apply` overwrites, keeping the old version under `improvements/`.
- Card updates still require `review`/`apply_draft`; "self-modification taking effect automatically" is not accepted.

### Security boundaries

- Synthesized scripts are lazy skeletons: no network, no subprocess, read-only by default; manual review is required.
- command/`allow=true` Cards are never applied automatically; high-risk candidates go through manual handling.
- The task trace is truncated and size-limited; no secrets are written; candidates do not automatically download/copy external code.
- All propose/approve/improve events are appended to `.caprouter/synthesized/synthesized.jsonl`,
  and the CLI clearly tells the user the candidate path and the next command.

## 17. Next steps

For priorities, acceptance metrics and the kill gate see `docs/roadmap.md`.
