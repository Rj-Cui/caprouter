# Capability Bus contract (host perspective)

CapRouter 0.7.0 is primarily a **local capability bus**: the host agent owns the
main loop, and CapRouter provides `search / get / invoke / verify / probe /
outcome / gaps / discover / health / duplicates / lock / drift` over MCP.

## 1. Standard flow

1. **Plan**: split subtasks; write inputs, outputs and acceptance criteria.
2. **Select**: `capability_search(task, capabilities?, domains?)`.
   - `select`: use `selected`;
   - `abstain`: do not force a choice; check gaps, rephrase, generate a script,
     or ask the user.
3. **Execute**:
   - prompt mode: `capability_invoke` returns a content path; read and execute it;
   - command mode: pass `allow_execute=true` only with explicit approval;
   - do not guess a capability by name; read the Card's capabilities/examples/probe.
4. **Verify**: `capability_verify(artifact, acceptance)`.
   - only `pass` means done; `fail`/`unverifiable` returns issues for rework, at
     most 2 rounds.
5. **Record**: `capability_record_outcome` records
   success/duration/human_edit_minutes.

## 2. Capability Card fields

| Field | Meaning |
|---|---|
| `id` / `type` / `name` | capability identity; type = skill / plugin / project / script / cli-agent |
| `capabilities` / `domains` | capability tags and domains |
| `input_schema` / `output_schema` | input/output contract (can be filled in via the overlay) |
| `examples` | examples of tasks it has done successfully |
| `probe` | status / evidence / pass_rate / checks / failures / latency |
| `invoke` | `mode: prompt` returns a content path; `mode: command` executes; `allow` must be true |
| `permissions` | fs / net / shell permission declarations |
| `provenance` | source, version, hash |
| `health` | whether the path/CLI is available |
| `stats` | calls / success_rate / avg_duration_ms / avg_human_edit_minutes |

## 3. Probe evidence

- `static`: path, SKILL.md lint, plugin manifest, invoke content, CLI
  availability. It only proves "it installs", not "it does the job well"; search
  gives it only a very small bonus.
- `behavioral`: the host actually ran a task and backfilled the result; search
  gives the full bonus.
- `mixed`: both.
- `fail`: down-weighted.
- `pending` / `unknown`: unproven, no bonus.

After the host finishes a pending host task:

```text
capability_probe_record(id, task, status="pass"|"fail", duration_ms?, notes?)
```

`host` tasks inside `probe.checks` are not executed automatically; `command`
probes require triple authorization.

## 4. MCP tools

| Tool | Purpose |
|---|---|
| `capability_search` | top candidates + confidence + abstain |
| `capability_get` | full Card |
| `capability_invoke` | prompt returns a content path; command executes and returns stdout/stderr |
| `capability_verify` | deterministic acceptance; returns verdict + checks |
| `capability_record_outcome` | records success / duration / human_edit_minutes / rework |
| `capability_record_gap` | records a missing capability |
| `capability_list_gaps` | lists gaps |
| `capability_probe` | runs deterministic probes (one or all) |
| `capability_probe_record` | backfills behavioral evidence from the host |
| `capability_probe_report` | views probe history and summary |
| `capability_card_draft` | generates a Card draft (read-only) |
| `capability_discover` | read-only search of remote skills.sh candidates (needs `allow_network=true`; no download, no install) |
| `capability_install` | local directory/ZIP; preview first, copy only with `approve=true` + `approve_hash` |
| `capability_uninstall` | preview/confirm removal of a managed capability; the external original directory is untouched |
| `capability_versions` | current version and historical versions in trash |
| `capability_health` | derives lifecycle from probe/outcome |
| `capability_duplicates` | duplicate copies across roots |
| `capability_lock` / `capability_drift` | lockfile / drift |
| `capability_deps` | read-only dependency plan/check; does not run pip/npm |

MCP server start: `python bin/caprouter.py mcp --workspace <workspace>`.
The plugin starts it through the root `.mcp.json`; under Claude Code it appears
as `plugin:caprouter:caprouter`.

## 5. Acceptance checks

`capability_verify` does not call an LLM by default. Structured check:

```json
{"type": "char_range", "min": 680, "max": 920, "unit": "han"}
{"type": "min_items", "value": 8, "pattern": "url"}
{"type": "exact_items", "value": 3, "pattern": "list"}
{"type": "format", "value": "markdown_table"}
{"type": "json_required", "value": ["claims", "sources"]}
{"type": "claim_ledger", "path": "claims/ledger.json"}
```

Free-text criteria try to parse common patterns ("150-200 Chinese characters",
"800±15% characters", "at least 8 sources", "exactly 3 items", "Markdown
table"); criteria that cannot be decided by machine return `unverifiable` — do
not treat them as pass.

Verdict rules: any `fail` -> `fail`; no fail but some `unverifiable` ->
`unverifiable`; all pass -> `pass`. Only `pass` lets you tell the user it is
done. `artifact_path` resolves relative to the workspace.

## 6. Card drafts and local install

- `capability_card_draft` (MCP) only returns a draft; it does not write the overlay;
- CLI `capability draft --apply` merges it; by default only missing fields are filled;
- CLI `install --source <dir|zip> --approve` installs a local capability:
  copy only, no execution, writes the manifest, refreshes the registry, and
  generates a draft without auto-applying it;
- CLI `uninstall` / `rollback`: preview by default; `--yes` moves the directory
  and restores the Card;
- online content may only go `discover -> fetch -> quarantine -> vendor -> install --approve -> review`;
  `fetch` only writes quarantine; it does not install or execute; automatic
  install is still not allowed.

## 7. Security

- `mode: prompt` only returns content; it does not execute;
- `mode: command` requires four things to be true at once: the Card's
  `invoke.allow=true`, the config's `bus.allow_execute=true`, the caller passing
  `allow_execute=true`, and the Card's `permissions.shell=true`; when `state` is
  off, everything is denied;
- command must be an argv list; it never goes through a shell;
- default cwd is `.caprouter/scratch`; the timeout cap is 600s;
- automatically installing/executing unknown plugins is out of scope for this
  version; record a gap first, then review manually.

## 8. bus mode vs orchestrator mode

- `mode: bus` (default for new workspaces): the host owns the main loop; the old
  `PreToolUse` guard lets host tools through; CapRouter only intercepts commands
  at `capability_invoke` according to Card permissions.
- `mode: orchestrator`: old behavior; the guard restricts
  WebSearch/WebFetch/Task/Bash/Write, and `caprouter run` dispatches nodes.
  A 0.2 workspace has no `mode` field and is treated as orchestrator. This is a
  compatibility path; no new features will be added.
