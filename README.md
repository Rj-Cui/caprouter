# CapRouter

[![tests](https://github.com/Rj-Cui/caprouter/actions/workflows/tests.yml/badge.svg)](https://github.com/Rj-Cui/caprouter/actions/workflows/tests.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![release](https://img.shields.io/github/v/release/Rj-Cui/caprouter)](https://github.com/Rj-Cui/caprouter/releases)

**A local capability ledger for AI agents.** Scan the skills, plugins, MCP servers and CLI agents
already on your machine, turn them into auditable Capability Cards, verify outputs deterministically,
and carry the reviewed set across hosts or machines — over MCP or a zero-dependency CLI.

- **Not** a marketplace, a model gateway, or a replacement for native tool search.
- **Is** the evidence layer between "found a skill" and "trust it in production".

> **First time here?** On Windows, download the repository and double-click `install.bat` — it finds
> Python, creates a workspace and installs a `caprouter` command, with no `pip` and no network access
> required. Jump to [Quickstart](#quickstart) or, for DeepSeek Harness, to
> [Connect to DeepSeek Harness (DSH)](#connect-to-deepseek-harness-dsh).

## The problem

Power users accumulate dozens of skills, plugins and MCP servers across Claude Code, Cherry Studio,
Cursor and other hosts. Three questions get hard:

1. What is actually installed on this machine, and which copy wins when roots overlap?
2. Does a capability still work after an upgrade, or does it only have a nice description?
3. When I move to another machine, how much of this reviewed setup can I keep?

Native tool search solves *selection* inside one host. Registries such as skills.sh solve *discovery*.
CapRouter covers the gap between them: **local inventory, evidence and portability**.

## Supported hosts

CapRouter is **MCP-native**, not a DSH-only plugin: any host that can speak MCP over stdio can call it
with `caprouter mcp --workspace <ws>`.

| Host | Connect | One-command setup | Host-aware loadability |
|---|---|---|---|
| **DeepSeek Harness (DSH)** | stdio MCP + config-only bundle | `setup --host deepseek-harness` | ✅ tested live |
| **Claude Code** | stdio MCP via `.mcp.json` / plugin root | `setup --host claude-code` | ✅ |
| **Claude Desktop** | stdio MCP | `setup --host claude-desktop` | config only |
| **Cursor** | stdio MCP | `setup --host cursor` | ✅ |
| **VS Code** | stdio MCP | `setup --host vscode` | ✅ |
| **Cherry Studio** | stdio MCP | `setup --host cherry` (print-only) | ✅ |
| **Codex** | stdio MCP (any MCP client) | scanned roots | ✅ |
| **Gemini CLI** | stdio MCP (any MCP client) | scanned roots | ✅ |

Any MCP-capable host connects with `caprouter mcp --workspace <ws>`. `--host <id>` decides which skill
roots are checked for loadability and where `link_host` copies installed skills. `setup --host` writes
host config for six of them; DSH gets a ready-to-add plugin bundle. Live-tested: DSH 0.2.2, Claude Code
plugin shape, Cherry Studio MCP; the remaining adapters generate config or scan roots only.

## What it does

CapRouter's primary shape is a **local capability ledger / capability bus**: the host agent (Claude
Code, Cherry Studio or any other MCP client) owns the main loop, and CapRouter supplies the inventory,
evidence and portability around it.
Two-stage routing is a supporting capability; it does not compete with the host's native tool search.

- **Scan** — skills, plugins, projects and CLI agents from configured roots; plus **MCP server
  inventory** from Claude/Cherry/Cursor/Codex/Gemini/VS Code configs. Env values are redacted; only
  env keys are stored.
- **Index** — Capability Cards with capabilities, domains, examples and optional probe checks.
- **Route** — lexical recall + rerank with `confidence` / `margin` / `coverage`; returns `abstain`
  instead of guessing.
- **Verify** — deterministic acceptance checks (char range, item counts, JSON, markdown, claim ledger,
  hashes) with no `pass` unless the checks actually pass.
- **Probe** — deterministic checks prove that a capability "installs, is found and can be invoked";
  behavioral evidence is recorded by the host via `probe-record`.
- **Synthesize (post-task only)** — only after local/trusted sources had no suitable capability and a
  gap was recorded, turn the task trace into a candidate skill/script; candidates first enter a local
  synthesis library, can be refined with built-in templates / `skill-creator` principles / CLI
  adapters, and must be approved by a human before they are registered.
- **Track** — append-only `outcomes.jsonl` and `probes.jsonl`; `prune` rotates them.
- **Adopt** — copy an existing skill/plugin into `installed/` for managed lifecycle; the original path
  is untouched.
- **Install integrity** — preview hash pinning, provenance `content_hash` verification,
  license/dependency metadata, symlink refusal by default, and an install journal.
- **Update** — `update`, `versions`, `rollback --version/--hash`; old copies stay in `installed/.trash`.
- **Diagnose** — `duplicates`, `health` (draft/probation/active/degraded/retired), `lock`, `drift`,
  and a static HTML `dashboard`.
- **Dependencies (plan-only)** — `deps plan` / `deps check` read package.json, pyproject, requirements
  and lockfiles; print exact install commands, risk flags and missing packages. CapRouter never
  executes pip/npm itself.
- **Signatures** — intentionally not implemented. The hash chain proves integrity, not publisher
  identity.
- **Migrate** — export reviewed Cards + evidence, import on a new machine; unmatched drafts can be
  `remap`ped.
- **Quarantine** — discover on skills.sh, fetch into an isolated directory, vendor a deconstruction
  report, then require explicit human approval to install. No automatic install, no script execution
  at install time. A generic `fetch --url` accepts ZIPs from an allowlist of hosts.

## Where it fits

| Layer | Examples | CapRouter's stance |
|---|---|---|
| Native selection | Claude / OpenAI tool search | Do not compete; expose Cards and evidence the host can use |
| Discovery & distribution | skills.sh, MCP registries | Discover only, then quarantine; do not build another marketplace |
| Gateways / model routing | MetaMCP, CC Switch, Claude Code Router | Out of scope |
| Local inventory / evidence / portability | **CapRouter** | Core focus |

If you only need to find and install skills, use skills.sh and your host's native search. CapRouter is
for the cases where you need to know **what is installed, whether it still works, and how to move it**.

## Why the name CapRouter

- `Cap` = capability, not "Super"; it describes a capability and promises nothing about being
  all-powerful.
- `Router` = routing, not "Orchestrator"; it sends a task to a suitable executor instead of pretending
  to do everything itself.
- CLI name: `caprouter`; the Chinese name translates to "capability routing".

## What's new in 0.7.0: host awareness, DSH adapter, one-click install

- **Host awareness**: search/detail results carry `host.loadable_by_host`; skills that belong to another
  host are flagged with a migration hint, and `prefer_host` (on by default) prefers candidates the
  current host can load; `link_host` / `unlink_host` keep install and uninstall in sync with the host
  skill directory (hand-edited copies are protected by hash).
- **DeepSeek Harness**: `setup --host deepseek-harness` generates a one-click-addable MCP plugin
  directory with the 10 core `mcp__caprouter__*` tools in DSH; `--link-skills` syncs managed skills into
  `~/.dsh/skills`.
- **Windows one-click install**: `install.ps1` + `install.bat`, no pip and no network, creating a
  workspace automatically.
- **Release packaging**: `release.ps1` builds `dist/caprouter-<version>.zip` plus SHA256 and release
  notes.
- Audit events are written per workspace; the test suite is reproducible from any path.

## What's new in 0.6.3: online quarantine discovery, pipelines, install lifecycle, candidate refinement

- **MCP inventory**: `mcp-list` and registry `type: mcp`; reads Claude/Cherry/Cursor/Codex/Gemini/VS
  Code and project `.mcp.json`, recording env keys only, never values.
- **Host roots**: new `.agents` / `.codex` / `.gemini` / `.cursor` / `.github` roots, project-local
  skills and the macOS Cherry path; `--skill-root` / `--plugin-root` can add more roots.
- **External skill takeover**: `unmanaged` / `adopt` / `backup-skill`; adopt copies into `installed/`
  and **leaves the original directory untouched**.
- **Install integrity**: preview hash pinning (a source that changes after the preview is refused),
  `provenance.content_hash` verification, manifests carrying license/dependencies/min version,
  payload-only, symlinks refused by default, and an install transaction log.
- **Batch Cards**: `capability draft --all --type skill|plugin` and `bootstrap --draft-registry`; no
  longer limited to projects.
- **Update and versions**: `update`, `versions`, `rollback --version/--hash`.
- **Diagnostics**: `duplicates` (duplicate copies across roots), `health`
  (draft/probation/active/degraded/retired), `lock` / `drift`, `dashboard` (static HTML).
- **Host config**: `setup --host claude-desktop|cursor|vscode|claude-code|cherry|deepseek-harness`
  prints by default and backs up before `--write`; DSH generates a ready-to-add bundle and
  `--link-skills` syncs managed skills to `~/.dsh/skills`.
- **Maintenance**: `prune` rotates the JSONL logs, `trash` lists/cleans `installed/.trash`.
- **Dependency plan**: `deps plan/check` only produce pip/npm commands and risk flags from read-only
  input, they **never execute**; installation is done explicitly by the host or the user.
- **Signatures**: not implemented for now; there is only the hash chain.
- **Remote**: `discover` with description/license (when upstream provides them), offline cache,
  `fetch --url` (allowed domains + size limits), `vendor --dir`.
- **Online discovery**: `discover → fetch → quarantine → vendor → install --approve → review`; skills.sh
  is searched read-only, downloads only enter quarantine, nothing is installed or executed
  automatically.
- **Pipeline**: `pipeline plan/run/verify/outcome`; command steps run deterministically, prompt steps
  stop at `awaiting_host`. Pipeline definitions are user-authored JSON in
  `<workspace>/.caprouter/pipelines/*.json` — no pipeline ships built in; start from
  `examples/pipeline.example.json`.
- **Install lifecycle**: `uninstall` / `rollback` preview by default and only change files with
  `--yes`; targets move into `installed/.trash/` and are recorded.
- **Candidate refinement**: 0.6.2's `synthesize` could already create candidates, but the content came
  from templates; 0.6.3 adds refinement with existing tools and makes sure new users have an authoring
  tool out of the box.

### Three refinement methods

```bash
caprouter synthesize refine --workspace <ws> --candidate <slug> --method auto
caprouter synthesize refine --workspace <ws> --candidate <slug> --method builtin
caprouter synthesize refine --workspace <ws> --candidate <slug> --method skill-creator --agent qwen
caprouter synthesize refine --workspace <ws> --candidate <slug> --method adapter --agent codebuddy
caprouter synthesize approve --workspace <ws> --candidate <slug> --auto [--apply]
```

`--auto` uses the project's built-in review and does not add another judging system:

- first install the candidate and generate a real Card draft (preferring the candidate's
  `card_draft.json` instead of letting heuristics re-infer permissions);
- review that real Card with `review_drafts`;
- only `auto_approved` keeps the install and the registry; otherwise the newly installed directory and
  the draft are removed and the registry refreshed, the candidate returns to / stays `proposed`, and no
  installed garbage is left behind;
- with `--apply`, the overlay is written through the same review flow once the review passes.

- `builtin`: needs no external tool at all; fills in When to use / Inputs / Workflow / Outputs / Checks
  / Failure handling from the trace template; always available.
- `skill-creator`: reads `skill:skill-creator` from the local registry as the writing standard, then
  hands over to a CLI adapter to produce strict JSON that rewrites `SKILL.md` and the Card
  description/examples.
- `adapter`: point directly at a local CLI adapter (qwen / codebuddy / kimi).
- `auto` (default): prefer skill-creator when present, otherwise look for a usable adapter, otherwise
  fall back to builtin; any failure leaves the original candidate intact.

Refinement only applies to `proposed` candidates; it does not change status, register, or execute.
Every refinement backs up the old file to `refinements/<timestamp>/SKILL.md.before` and writes the
method, adapter and warnings into `candidate.json`.

### Built-in capability-author

A new `skills/capability-author/` directory appears in the plugin root and shows up automatically with
the registry scan:

- Triggers: `capability search` abstains or top-1 is clearly a mismatch; after a task ends, a
  skill/script is worth keeping.
- Workflow: confirm the gap → collect the trace → propose → refine → human review → approve →
  probe/outcome → improve.
- Hard rules: never synthesize without a gap; candidates are not registered; scripts are not executed
  automatically; nothing outside the trace is invented; improvements need another approval.
- Ships `references/authoring-checklist.md` (five check groups: trigger, candidate, Card, approval,
  improvement).
- The slash command `/caprouter:synthesize` provides the propose/list/refine/approve/improve entry
  points.

New users can complete authoring with `builtin` + `capability-author` even without `skill-creator` or
any CLI adapter; external tools are an enhancement, not a prerequisite.

## What's new in 0.6.2: post-task synthesis

Once `capability search` finds no trustworthy capability and records a gap, at the end of the task you
can:

```bash
caprouter synthesize propose --workspace <ws> --task "<task>" --trace <trace.json> --name <slug> --type skill
caprouter synthesize list --workspace <ws>
caprouter synthesize approve --workspace <ws> --candidate <slug> [--apply]
caprouter synthesize improve --workspace <ws> --id skill:<slug> [--apply]
```

Rules:

- **Only after a gap**: without a `capability gap` record, `propose` returns `no_gap` directly;
- **Candidate first, registration later**: candidates are written to
  `.caprouter/synthesized/<slug>-<hash>/` and are never installed automatically;
- **Only approve registers**: `approve` goes through the existing installer, refreshes the registry,
  generates a Card draft and runs the deterministic review;
- **No automatic script execution**: `--type script` generates an inert skeleton (no network, no
  subprocess) that must be reviewed by a human;
- **Continuous improvement**: `improve` reads `outcomes.jsonl` and produces a versioned patch plus
  `card_patch.json`; only `--apply` replaces the installed skill;
- Every propose/refine/approve/improve appends to `.caprouter/synthesized/synthesized.jsonl` and says
  so explicitly in the CLI.

## What's new in 0.6.1

- **Risk model fix**: `risk` means "is it safe to write this Card into the overlay", and
  evidence-backed net/write/shell declarations are no longer automatically medium; declarations
  without evidence are still medium, and command/allow=true is still high. This way prompt/none Cards
  that only write metadata and execute no code are not penalised by their permission fields.
- **Medium auto-apply fix**: a medium Card with `allow_medium=true` and a semantic pass now really
  writes to the overlay; previously a hard-coded `risk == "low"` check blocked it, so the state showed
  auto_approved while applied=0.
- **`review --fix`**: the semantic layer can return a validated `repaired` field
  (description/examples/capabilities/keywords); after repair the deterministic review and an
  independent semantic re-check run once more. Permissions and invoke are never rewritten.
- **Semantic isolation and caching**: CodeBuddy uses `--tools ""`, Qwen uses `--approval-mode plan` to
  disable tools; Kimi has no verified isolation flag and is refused by default (explicitly overridable
  with `semantic.allow_unisolated=true`). Semantic results are cached by Card + evidence + rubric, so
  repeated reviews do not pay twice.
- **Migration identity fix**: the metadata hash now covers only relative file names and contents, no
  longer absolute paths/mtime; project id conflicts across machines require a second confirmation via
  manifest/hash/path hint; command/allow Cards in a bundle are not auto-applied but written as
  `migrated_unsafe` drafts in `drafts/migrated/`.
- **Scan noise reduction**: directories that hold only `settings.gradle` / `CMakeLists.txt` and no
  README/source directory (typically the `support/` of a vendored tool) no longer count as projects;
  explicitly passed roots are still kept.
- **Evidence extension**: read-only parsing of Gradle / Maven / CMake metadata; a new `has_build`
  signal.
- Example truncation now respects sentence/word boundaries so the 200-character cut does not land
  mid-word.

## What's new in 0.6.0

- `caprouter/review.py`: deterministic Card review (id/path, description, examples, capabilities,
  permissions, invoke, retrieval replay, risk grading); no model calls.
- `caprouter/evidence.py`: read-only evidence extraction (package.json scripts/bin, pyproject/Cargo
  entrypoints, README commands); no code execution.
- `caprouter/semantic.py`: built-in Card review rubric plus an optional rubric extracted from a local
  skill, returned as strict JSON through a CLI agent; used only for medium-risk re-checks.
- `caprouter/migrate.py`: `migrate export/import`, matching by id / registry hash / manifest name /
  path hint; matched reviewed Cards are applied automatically, unmatched ones are written to
  `drafts/migrated/`.
- `bootstrap` now runs the deterministic review after drafting by default; `--no-review` turns it off.
- Graded auto-apply: only low risk (and meeting the examples/capabilities/retrieval thresholds) enters
  the overlay automatically; medium needs `allow_medium=true` plus a semantic pass; command/allow=true
  is never automatic.
- `caprouter review --semantic` calls a local CLI agent for drafts that did not pass automatically; it
  is expensive, so it is off by default.

## What's new in 0.5.0

- `scan-projects`: scans only **non-system fixed drives** or an explicit `--project-root`;
  `%SystemDrive%`, `Program Files`, `AppData`, `node_modules`, `.venv`, hidden/system directories and
  reparse points are skipped by default; each root has `max_depth` / `max_dirs` / `time_budget` /
  `max_projects` budgets, and anything over budget is recorded in `truncated` rather than silently
  cut.
- `bootstrap`: chains `init if missing → scan → merge registry → batch draft → static probe` in one
  command; it **only generates drafts and never applies automatically**; `--dry-run` writes no files
  at all.
- `capability draft --all`: batch-generates drafts from `.caprouter/projects_scan.json` into
  `.caprouter/drafts/projects/`; `--apply` has to be triggered explicitly by a human.
- Scanning collects metadata only (path, marker, mtime, whether a README exists); it does not read
  source code and does not execute anything; the full list goes to `projects_scan.json`, and the
  summary handed to a model/terminal is capped at ≤ 4KB.
- Project Card `description` / `capabilities` / `keywords` / `domain` are inferred deterministically
  from `package.json`, `pyproject.toml`, README and the like; after apply they participate in routing
  through the overlay immediately, with no index rebuild.
- Fix: metadata fields in the overlay now take part in `capability search` recall and capability
  filtering, not just examples.

## What's new in 0.4.0

- `capability probe`: built-in static probes (path, `SKILL.md` lint, plugin manifest, invoke content,
  CLI availability) plus optional command probes; `probe-record` backfills behavioral evidence recorded
  by the host; `probe-report` shows the history.
- `capability draft`: generates Card drafts (examples / probe / permissions / invoke) from `SKILL.md` /
  README / `plugin.json`; by default it only prints drafts, and `--apply` writes the overlay.
- `install` / `installed`: local directory/ZIP installer; `--approve` is mandatory, it only copies
  files and executes no scripts, writes a manifest (source/version/hash/permissions/warnings),
  refreshes the registry and generates a Card draft.
- Search ranking distinguishes **static** and **behavioral** probe evidence: a static pass adds only a
  small bonus, a behavioral pass adds the full bonus, and failures are down-weighted uniformly.

## Quickstart

Requires Python 3.11+; the CLI and MCP server have no third-party runtime dependencies.

**Option 0: one-click install (Windows; recommended if you would rather not touch a terminal)**

1. Download `caprouter-<version>.zip` from
   [Releases](https://github.com/Rj-Cui/caprouter/releases) (or grab the repository ZIP) and extract it
   (keep the path free of special characters if possible);
2. Double-click `install.bat`;
3. Close the window once it says the install is complete; from then on `caprouter` works in any new
   terminal.

The script copies the app to `%LOCALAPPDATA%\CapRouter` (it keeps working even if you delete the
repository), creates a workspace at `%USERPROFILE%\CapRouter\workspace`, and runs `init` plus one search
demo; **no pip, no network**. When it detects DeepSeek Harness it asks whether to connect it as well.

Optional flags: `install.bat -Dsh` (connect DSH directly), `-SkipBootstrap`, `-NoPath` (do not change
PATH), `-Force` (overwrite the installation), `-Yes` (do not ask).

See `docs/install-windows.md` for the details.

**Option 1: run from the repository (cross-platform)**

```bash
git clone https://github.com/Rj-Cui/caprouter.git
cd caprouter
python bin/caprouter.py --help
```

**Option 2: install as a command**

```bash
pipx install .        # or pip install .
caprouter --help
```

> A pip install needs network access to build (`setuptools>=77`); without a network, use Option 0 or
> Option 1.

The examples below use `python bin/caprouter.py`; replace it with `caprouter` once installed.
The pip package contains only the CLI and MCP server; hooks, slash commands and built-in skills are host
plugin assets — Claude Code loads them from the repository root, and DSH is covered in the next section.

```bash
# 0. First load: scan non-system fixed drives -> dedupe -> batch drafts -> review -> static probes
python bin/caprouter.py bootstrap --workspace ./workspace
# Deterministic review; only --auto-apply writes low-risk passing Cards into the overlay
python bin/caprouter.py review --workspace ./workspace --auto-apply
# Optional: semantic re-check for drafts that did not pass (calls a local CLI agent; expensive)
python bin/caprouter.py review --workspace ./workspace --semantic --agent qwen --auto-apply
# Machine migration
python bin/caprouter.py migrate export --workspace ./workspace
python bin/caprouter.py migrate import --workspace <new-workspace> --bundle <bundle.zip>

# 1. Init: scan local capabilities and build the registry
python bin/caprouter.py init

# 2. Capability search (abstains when it is not confident)
python bin/caprouter.py capability search --need "convert this PDF to Word" --top 5

# 3. Inspect a Card
python bin/caprouter.py capability get --id "skill:demo-office-doc"

# 4. Deterministic acceptance
python bin/caprouter.py capability verify \
  --artifact out.md --criterion "150–200 characters" --criterion "exactly 3 items"

# 5. Probes: run deterministic checks, then backfill host execution results
python bin/caprouter.py capability probe --id "skill:demo-office-doc"
python bin/caprouter.py capability probe-record \
  --id "skill:demo-office-doc" --task "convert page 3 of the PDF to Word" --pass --duration-ms 1200
python bin/caprouter.py capability probe-report --id "skill:demo-office-doc"

# 6. Card drafts: generate -> human review -> apply
python bin/caprouter.py capability draft --id "skill:demo-office-doc"
python bin/caprouter.py capability draft --id "skill:demo-office-doc" --apply

# 7. Local install: preview first, then approve manually
python bin/caprouter.py install --source "D:/downloads/my-skill"
python bin/caprouter.py install --source "D:/downloads/my-skill" --approve
python bin/caprouter.py installed

# 8. Post-task synthesis: candidates only after a gap; refine is optional, only approve registers
python bin/caprouter.py synthesize propose --workspace <ws> --task "<task>" --trace <trace.json>
python bin/caprouter.py synthesize refine --workspace <ws> --candidate <slug> --method auto
python bin/caprouter.py synthesize approve --workspace <ws> --candidate <slug> [--auto]

# 9. MCP server (the Claude Code plugin starts it automatically through the root .mcp.json)
python bin/caprouter.py mcp --workspace ./workspace
```

The first `init` writes:

```
<workspace>/.caprouter/config.json
<workspace>/.caprouter/registry.json
<workspace>/.caprouter/plans/
<workspace>/.caprouter/scratch/
<workspace>/deliverables/            (created by init; --delivery overrides it)
```

`scan-projects` / `bootstrap` additionally write:

```
<workspace>/.caprouter/projects_scan.json      full scan result
<workspace>/.caprouter/drafts/projects/        batch Card drafts (not applied)
```

Everything else under `.caprouter/` is created on first use (runs, reviews, probes, outcomes, installed,
pipeline-runs, migrations, synthesized, quarantine).

## Capability bus

### Search

`capability search` does two-stage recall + rerank:

- lexical recall (BM25 + Chinese/English alias expansion);
- capability tag matching, example overlap, historical success rate;
- probe evidence: `probe.status=pass` with behavioral/mixed evidence gets the full bonus; static-only
  gets only a small bonus; `fail` is down-weighted;
- negative description penalty ("reading only / not supported" is down-weighted for create/convert
  tasks);
- `decision=select|abstain`, returning confidence / margin / coverage / thresholds.

The defaults are `min_confidence=0.18`, `min_margin=0.02`, `min_coverage=0.12`; they can be overridden
in the config `bus` block or the overlay `defaults`.

### Verification

`capability verify` does not call an LLM by default and supports:

- `file_exists` / `non_empty`
- `char_range` / `min_chars` / `max_chars` (Chinese characters or characters)
- `min_items` / `max_items` / `exact_items` (list/url/table/paragraph/source_ref)
- `format` (json / markdown / markdown_table)
- `json_required`
- `claim_ledger` (reusing the snapshot + hash + quote checks of `caprouter verify --claims`)
- `contains` / `not_contains` / `regex` / `sha256`

Free-text criteria are parsed against common patterns; anything a machine cannot judge returns
`unverifiable` and must not be treated as a pass. Verdict: any fail → fail; no fail but some
unverifiable → unverifiable; all pass → pass.

### Probes

`capability probe` definitions live in the overlay's `probe.checks`:

```json
{
  "probe": {
    "min_pass_rate": 0.8,
    "checks": [
      {"id": "host-1", "type": "host", "task": "convert page 3 of the sample PDF to PNG"},
      {"id": "cmd-1", "type": "command", "command": ["python", "tool.py"], "allow": true,
       "acceptance": [{"type": "contains", "value": "ok"}]}
    ]
  }
}
```

Rules:

- Built-in static checks always run: path, `SKILL.md` lint, plugin manifest, prompt content, CLI
  availability; with `--hash` the content hash is compared as well.
- `host` checks are not executed automatically, they only return pending; after the host runs them it
  calls `capability probe-record` (or the MCP `capability_probe_record`) to backfill the result.
- `command` checks require all of `check.allow=true`, the Card's `permissions.shell=true`, config
  `bus.allow_execute=true` and the caller's `allow_execute=true`; argv lists are used, never a shell.
  With `state` off, command checks are simply skipped.
- Other deterministic checks (`char_range`, `min_items`, `format`, ...) can apply to `path` / `text` /
  the capability body.
- Probe history goes to `.caprouter/probes.jsonl`, append-only; the overlay is only the initial seed.
- `probe.status=fail` is down-weighted; `pending`/`unknown` add nothing. **A static pass does not mean a
  capability works** — that is deliberate.

### Card drafts

`capability draft` generates deterministically from local content:

- `examples`: first try to parse sections such as `## Examples` / `## Usage` (localized heading
  variants are recognised too), then split sentences from the description;
- `probe.checks`: the first 3 examples become host tasks;
- `permissions`: fs/net/shell inferred from keywords and the `scripts/` directory;
- `invoke`: skill/plugin default to prompt mode, project defaults to none;
- project description / capabilities / keywords / domain are inferred deterministically from
  `package.json`, `pyproject.toml`, README and the like, without calling a model.

Drafts never enter the store automatically. `--apply` only fills in missing fields and keeps existing
non-empty ones; `--force` overwrites. Draft files live in `.caprouter/drafts/`.
`capability draft --all` reads `projects_scan.json` and batch-generates into
`.caprouter/drafts/projects/`; unchanged drafts are skipped (`unchanged`), and only an explicit
`--apply` or `--force` changes files.

### Scanning and bootstrap

```bash
# Read-only: scans non-system fixed drives by default
caprouter scan-projects --workspace <ws> --json

# Pick a drive / an explicit root (--project-root may point at one project or at a directory of projects)
caprouter scan-projects --workspace <ws> --drive D --project-root "D:/work"

# Merge the scan result into the registry
caprouter scan-projects --workspace <ws> --drive D --merge-registry

# First load (creates the workspace when init is missing): scan -> registry -> drafts -> static probes
caprouter bootstrap --workspace <ws> --drive D

# Clean workspace: skip the machine's global skill/plugin roots and keep only the given project root
caprouter bootstrap --workspace <ws> --project-root D:/work --no-default-skills
```

Boundaries:

- By default only `DRIVE_FIXED` drives that are not `%SystemDrive%` are scanned; network, optical and
  RAM drives are never scanned by default; removable drives need `--include-removable`;
- System/software/cache directories (Windows, Program Files, AppData, SteamLibrary, node_modules,
  .venv, .git, dist, build, ...), hidden/system directories and reparse points/junctions (not followed)
  are skipped;
- Each root has its own budget: `max_depth=4`, `max_dirs=50000`, `time_budget_seconds=30`,
  `max_projects=500`, overridable in the config `scan` block;
- Only path/name/marker/mtime/has_readme are collected; README bodies and source code are not read and
  nothing is executed;
- Full list: `.caprouter/projects_scan.json`; the summary is ≤4KB (`recent_projects` gets trimmed);
- Scanned projects are deduplicated by real path and duplicates are recorded; a saved scan result keeps
  merging into the registry on later `scan`s;
- `--no-default-skills` leaves `skill_roots` / `plugin_roots` empty in the new workspace and keeps only
  the `--project-root` results, which suits isolated tests or dedicated workspaces;
- `bootstrap` never applies drafts automatically and never enables command execution automatically.

### Card review and auto-apply

```bash
# Deterministic review (no model calls)
caprouter review --workspace <ws> --json

# Review only the given id
caprouter review --workspace <ws> --id project:prompt-optimizer --json

# Semantic re-check + automatic apply of low/medium risk passes
caprouter review --workspace <ws> --semantic --agent qwen --auto-apply

# Semantic re-check asking for a verified rewrite, then review once more
caprouter review --workspace <ws> --fix --agent qwen --auto-apply
```

The review flow:

1. **Evidence extraction**: read-only `package.json` scripts/bin, `pyproject`/`Cargo` entrypoints,
   Gradle/Maven/CMake metadata, README commands; nothing is executed and no project directory is
   written.
2. **Deterministic review**: id/path consistency, readable description, usable examples, capabilities
   backed by evidence, permission/invoke conflicts, retrieval replay (query the registry with the
   examples and require top-1/top-3 hits).
3. **Risk grading** ("is it safe to write into the overlay"):
   - low: prompt/none invoke, permission claims backed by evidence, examples/capabilities/retrieval
     within thresholds;
   - medium: net/write/shell claims **without evidence**, too few examples, unknown tags, weak
     retrieval;
   - high: command invoke / `allow=true`, id/path mismatch, missing path.
4. **Auto-apply**:
   - low with score ≥0.8: written into the overlay automatically;
   - medium: only when `review.allow_medium=true` and the semantic re-check passes;
   - high or rejected: never applied automatically.
5. **`--fix`**: the semantic layer may return `repaired` (description/examples/capabilities/keywords),
   which after local validation rewrites the draft, then the deterministic review and a fresh semantic
   re-check run; permissions/invoke are never accepted as rewrites. Semantic results are cached by
   default, so unchanged Cards are not re-requested.

Config block:

```json
"review": {
  "auto_apply": false,
  "min_examples": 3,
  "min_capabilities": 1,
  "allow_medium": false,
  "retrieval": {"enabled": true, "top_k": 3, "min_top1_rate": 0.3, "min_top3_rate": 0.6},
  "semantic": {
    "enabled": false,
    "adapter": "qwen",
    "capability_id": "",
    "timeout": 120,
    "min_confidence": 0.75,
    "cache": true,
    "allow_unisolated": false
  }
}
```

An empty `semantic.capability_id` uses only the built-in rubric; filling in a local skill id (for
example `skill:skill-creator`) additionally extracts the review/quality checklist sections of that
skill. Semantic re-checks are off by default because one agent call per Card can take tens of seconds.
CodeBuddy uses `--tools ""`, Qwen uses `--approval-mode plan` to disable tools; Kimi has no verified
isolation flag and is refused by default — it is only called when `allow_unisolated=true` is set
explicitly.

Review results are written back to the draft's `review` field and to `.caprouter/reviews/latest.json`;
auto-applies are recorded in `.caprouter/reviews/applied.jsonl`.

### Device migration

```bash
# Old machine: export reviewed Cards + evidence
caprouter migrate export --workspace <old-ws> --out D:/migration.zip

# New machine: init/bootstrap --no-review first, then import
caprouter migrate import --workspace <new-ws> --bundle D:/migration.zip
```

Matching order: id → registry hash → manifest name → name → path hint.  
Matched reviewed Cards are written straight into the new overlay; unmatched ones go to
`.caprouter/drafts/migrated/` and are not silently dropped.  
Local projects on the new machine only get Cards and are not re-reviewed:

```bash
caprouter bootstrap --workspace <new-ws> --no-review
```

### Local install

```bash
caprouter install --source <local-dir-or-zip>          # preview
caprouter install --source <local-dir-or-zip> --approve # install
```

Installer boundaries:

- Offline by default; local directories/ZIPs install directly, and online content must pass through
  quarantine first;
- Installs into `<workspace>/.caprouter/installed/<type>/<name>/`;
- Only copies files and **executes no** scripts; ZIPs are rejected for path traversal, symlinks,
  encrypted members and oversized files;
- Writes `manifest.json` (source, version, hash, permissions, executables, warnings);
- Adds the installed directory to the config's `skill_roots` / `plugin_roots` automatically and
  refreshes the registry;
- Generates a Card draft automatically, but **never applies it automatically** and never enables
  command execution;
- With `--force`, the old directory is moved to `.caprouter/installed/.trash/` before overwriting.

### Online discovery (quarantine)

```bash
caprouter discover --query "pdf" --limit 10 --offset 0 --allow-network --json
caprouter fetch --candidate anthropics/skills/pdf --allow-network --json
caprouter vendor --candidate anthropics/skills/pdf --json
caprouter install --source "<workspace>/.caprouter/quarantine/anthropics-skills/pdf" --approve
caprouter quarantine list --json
caprouter quarantine clean --candidate anthropics/skills/pdf --yes
caprouter quarantine clean --older-than-days 30 --yes
```

Flow and boundaries:

- `discover`: read-only search of the public skills.sh catalog, returning candidate id / source /
  installs; no download, no install; supports client-side paging (`--offset` / `--limit`, the API
  returns at most 100 entries and `next_offset` gives the next page), 0.5s between requests, and one
  automatic retry on 5xx/429;
- `fetch`: downloads from skills.sh into `<workspace>/.caprouter/quarantine/<source>/<skill>/`;
  validates path, file count and total size; **does not install or execute**; writes `provenance.json`
  (source URL, hash, file list, fetch time, LICENSE detection result);
- `fetch` reads `LICENSE` / `COPYING` and recognises common licenses such as MIT, Apache-2.0,
  GPL/AGPL, BSD, MPL and CC-BY; unrecognised ones are written as `unknown` rather than guessed; this is
  a text heuristic, not a substitute for legal review;
- `vendor`: reads `SKILL.md` from quarantine and produces `deconstruction.md` plus `card_draft.json`
  following the six `skill-deconstruction` steps; it does not install;
- `install --approve`: reuses the existing installer to copy the quarantine directory into installed
  and refresh the registry;
- `review` / `capability draft --apply`: only a human review writes into the overlay;
- Offline by default, `--allow-network` must be explicit; with `state` off, `discover` / `fetch` /
  `vendor` are all refused; only the `skills.sh` domain is allowed.

### Install lifecycle

```bash
caprouter uninstall --workspace <ws> --id skill:pdf          # preview
caprouter uninstall --workspace <ws> --id skill:pdf --yes    # move to installed/.trash
caprouter rollback  --workspace <ws> --id skill:pdf          # preview
caprouter rollback  --workspace <ws> --id skill:pdf --yes    # restore directory and Card
```

- `uninstall` only accepts targets inside `installed/`; it previews by default and moves only with
  `--yes`;
- The target moves to `.caprouter/installed/.trash/`, and the registry entry and overlay Card are
  removed at the same time;
- The uninstall record is written to `.caprouter/uninstalled.jsonl`;
- `rollback` restores the most recent uninstall; if the target already exists it needs `--force`, and
  the old target moves to `.trash/...-pre-rollback/`.

### MCP tools

| Tool | Description |
|---|---|
| `capability_search` | top candidates + confidence + abstain |
| `capability_get` | full Card |
| `capability_invoke` | prompt returns a content path; command executes and returns a transcript |
| `capability_verify` | deterministic acceptance |
| `capability_record_outcome` | record success/duration/human_edit_minutes |
| `capability_record_gap` | record a missing capability |
| `capability_list_gaps` | list recorded gaps |
| `capability_probe` | run deterministic probes (single or all) |
| `capability_probe_record` | backfill behavioral evidence recorded by the host |
| `capability_probe_report` | show probe history and summary |
| `capability_card_draft` | generate a Card draft (read-only, does not write the overlay) |
| `capability_discover` | read-only search of remote skills.sh candidates (requires `allow_network=true`; no download, no install) |
| `capability_install` | local directory/ZIP; preview first, only `approve=true` + `approve_hash` copies; remote fetch stays CLI-only |
| `capability_uninstall` | preview/confirm removal of a managed capability; the external original directory is untouched |
| `capability_versions` | current version plus historical versions in `installed/.trash` |
| `capability_health` | lifecycle state derived from probe/outcome |
| `capability_duplicates` | duplicate copies across roots and the current winner |
| `capability_lock` / `capability_drift` | write a lockfile / compare drift |
| `capability_deps` | read-only dependency plan/check; returns commands and risk flags, never runs pip/npm |

`discover` only returns candidates; `fetch` / `vendor` remain CLI-only so that a host cannot download
untrusted code automatically.
`capability_install` must first be called with `approve=false` to obtain the `source_hash`, then with
`approve=true` + `approve_hash` to install; a hash mismatch returns `source_changed`.

### Inventory, diagnostics and lifecycle

```bash
caprouter mcp-list --workspace ./workspace                 # MCP server inventory (redacted)
caprouter unmanaged --workspace ./workspace                # skills/plugins not in installed/
caprouter adopt --workspace ./workspace --id skill:foo     # preview; --yes copies, original untouched
caprouter duplicates --workspace ./workspace               # duplicate copies across roots
caprouter duplicates --workspace ./workspace --prefer <path>  # make that root the winner
caprouter health --workspace ./workspace                   # lifecycle state
caprouter health --workspace ./workspace --probe           # run static probes first, then summarise
caprouter lock --workspace ./workspace                     # write the lockfile
caprouter drift --workspace ./workspace --check            # exit code 1 when drift is found
caprouter versions --workspace ./workspace --id skill:foo
caprouter update --workspace ./workspace --id skill:foo --source <dir>
caprouter setup --host cursor --workspace ./workspace      # prints by default; --write backs up first
caprouter dashboard --workspace ./workspace                # deliverables/caprouter-dashboard.html
caprouter deps plan --workspace ./workspace --id skill:foo   # generate commands only, never execute
caprouter deps check --workspace ./workspace --id skill:foo  # check the current environment
caprouter prune --workspace ./workspace --probes --keep 500
caprouter trash --workspace ./workspace --clean --older-than-days 30
```

The plugin-root `.mcp.json`:

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

## Legacy orchestrator (compatibility)

`mode: orchestrator` is the 0.2 plan/run behavior: the guard restricts WebSearch/WebFetch/Task/Bash/
Write, and `caprouter run` dispatches nodes. Old workspaces without a `mode` field are treated as
orchestrator, while new workspaces default to `mode: bus`; orchestrator is compatibility-only and no
longer receives new features.

- Do not use this path for new tasks; it is kept only so that old workspaces and old tests stay
  reproducible.
- `caprouter run` prints a compatibility warning.
- Phase 2 comparison data and conclusions are kept in internal evaluation notes and are not
  published; the public repository ships only synthetic benchmark templates.

## Connect to DeepSeek Harness (DSH)

DSH's extension unit is an npm bundle, not a Claude plugin, so `.claude-plugin/`, `hooks/` and
`commands/` do not apply in DSH. CapRouter connects to DSH as a **stdio MCP server**, and its built-in
skills load through the DSH skill root.

```bash
# 1) Generate a one-click-addable DSH plugin directory: <workspace>/caprouter-dsh-bundle/
python bin/caprouter.py setup --host deepseek-harness --write --workspace ./workspace

# 2) (Optional) sync CapRouter-managed skills into the DSH skill root (default: ~/.dsh/skills)
python bin/caprouter.py setup --host deepseek-harness --link-skills --workspace ./workspace
```

Then in DSH: **Plugins → Add local plugin directory → pick `caprouter-dsh-bundle`**. A new session then
exposes the **10 core** `mcp__caprouter__*` tools (`capability_search`, `capability_get`,
`capability_invoke`, `capability_probe`, `capability_probe_record`, `capability_verify`,
`capability_record_outcome`, `capability_list_gaps`, `capability_install`, `capability_uninstall`).
To get all 20, change `--tools core` to `--tools full` in the bundle.

The command above embeds this machine's Python path, the repository path and the workspace path, so the
result only works here. To produce a bundle you can hand to another machine (or ship to a plugin
catalog), generate the portable form instead:

```bash
# no absolute path from this machine: `python -m caprouter`, workspace resolved at startup
python bin/caprouter.py setup --host deepseek-harness --write --portable --workspace ./workspace
```

The portable bundle expects Python 3.11+ and `pip install caprouter` for that interpreter
(`CAPROUTER_PYTHON` overrides the interpreter, `CAPROUTER_WORKSPACE` the workspace). `tests/test_dsh_bundle.py`
asserts that a portable bundle contains no path from the machine that produced it.

Search results carry a host judgement: `health.loadable_by_host` being `false` means the capability
belongs to another host (for example Cherry Studio), cannot be loaded by DSH, and comes with a
migration hint; `prefer_host` (on by default) ranks host-loadable candidates first and switches the
selection when necessary. After a successful `capability_install`, the skill is copied into the current
host's skill root by default (`link_host`); `capability_uninstall` removes that host copy safely again
on uninstall (`unlink_host`; a copy that was edited by hand is kept).

- `--force` overwrites an old bundle / old skill copy; `--skills-root <dir>` picks a different skill
  root.
- `setup --host` only prints by default; add `--write` to persist anything.
- The MCP server binds the workspace and the absolute path of the Python used at generation time;
  **regenerate after moving the repository or switching workspace**.
- DSH times out each tool call after 60 seconds by default; the generated bundle raises
  `toolCallTimeoutMs` to 120 seconds, which can be raised further for long tasks (`probe --all`,
  `install`) when needed.

## Plugin form (Claude Code)

The repository root is itself the plugin root:

```
.claude-plugin/plugin.json   plugin manifest
skills/caprouter/SKILL.md    entry contract (≤2KB)
skills/capability-author/    built-in authoring tool: gap → trace → propose/refine → approve → improve
commands/                    /caprouter:init, bootstrap, review, migrate, synthesize, run, doctor, state
hooks/hooks.json             UserPromptSubmit auto reminder + PreToolUse policy
hooks/guard.py               orchestrator-mode guard; passes through in bus mode
hooks/user_prompt_submit.py  injects CapRouter instructions when the tray is on
tray/                        cross-platform tray toggle (pystray + Pillow)
mods/caprouter-mod/          Claude Code mod: prompt.submit takeover + /caprouter status command
eval/                        routing eval cases (dev + held-out)
docs/                        architecture, assessment reports, roadmap
bin/caprouter.py             CLI entry
caprouter/                   Python core
schemas/                     registry / plan contracts
tests/                       unit tests that do not depend on external models
```

Load the repository root as the plugin directory (use the current Claude Code `/plugin` help for the
exact install command).

## Status (0.7.2, 2026-11-25)

- **430 unit tests** in `tests/` (`python -m unittest discover -s tests`), zero runtime dependencies.
  Every test runs against a temporary workspace and an isolated home; none calls a model or the network.
- Prompt content paths are bound to the capability directory and the workspace; an explicit
  `--allow-external-content` is required for anything else.
- Host-aware search: capabilities owned by another host are flagged, not presented as usable.
- DeepSeek Harness adapter: `setup --host deepseek-harness` plus install/uninstall host linking.
- Windows one-click installer (`install.ps1` / `install.bat`) and a `release.ps1` packager.
- MCP server exposes **10 core tools** for hosts (`--tools core`, the default `setup --host` writes)
  or all 20 (`--tools full`). The bundled `skills/caprouter/SKILL.md` only calls tools that are in the
  core profile; if you switch a host to `full`, the extra tools become available but nothing in the
  skill depends on them.

### Verification log (0.6.3, 2026-11-23)

- Python tests: **316 passing** (`python -m unittest discover -s tests`); mod tests pass;
- Plugin static validation: `npx -y @anthropic-ai/claude-code@latest plugin validate .` passes
  (recorded 2026-11-21);
- Registry: the local example workspace holds **164 entries** (142 skill + 10 project + 7 plugin +
  3 CLI agent + 2 mcp); the number varies per machine and is not a project constant;
- MCP server: **20** `capability_*` tools (including the read-only `capability_discover`,
  `capability_health`, `capability_duplicates`, `capability_lock`, `capability_drift`);
- Security hardening: the state switch, Card permissions, guard bypass, uninitialised install, review
  scope, backup traversal and same-second refine cases are fixed; see
  `docs/security-hardening-2026-11-23.md`;
- Online discovery: `discover → fetch → quarantine → vendor` verified end-to-end on
  `anthropics/skills/pdf` (12 files, 68,603 bytes, Apache-2.0, **not installed**);
- `fiction-deslop` was cited here as a built-in pipeline; **it never shipped**. Pipeline definitions
  are user-authored JSON in `<workspace>/.caprouter/pipelines/*.json` (`id`, `goal`, `steps[]`), and
  `pipeline plan` selects the capability for each step. See `examples/pipeline.example.json`.
- Install lifecycle: install → uninstall → rollback verified in a temporary workspace;
- Hosts: the Claude Code plugin static validation passes; Cherry Studio MCP has been exercised with
  `capability_search` (select → `skill:demo-office-doc`, confidence 0.7195); a cross-host behavior
  matrix is still not systematic.

### Historical baseline (2026-11-21)

- Disk scan (D: + E:, read-only): 1537 directories / 166 candidates / 0.14s; 44 system/cache/weak
  project directories skipped; 851 directories were not descended further because of max_depth=4
  (reported); truncated 0;
- Review (dry-run): of 21 drafts, 18 were `needs_review` / medium and 3 `rejected` / high (old drafts
  for GPL/gradle/zoneinfo2, no longer in the registry); 0 automatic applies; the main reasons were too
  few examples, some permission claims without evidence, and weak retrieval;
- Semantic repair sample (qwen): 1 of 4 Cards became auto_approved and was applied; 2 passed but at
  confidence 0.72, below 0.75; 1 had weak retrieval replay. The repair pipeline works, but the
  thresholds were not lowered to hit targets;
- Migration drill (a simulated new workspace on the same machine): 2 Cards + 7 probes + 2 outcomes,
  matched 2/2, applied 2, unmatched 0;
- Post-task synthesis (a real gap): `skill:demo-game-designer` was refined with `--method auto`,
  then passed the built-in deterministic review through `synthesize approve --auto` (auto_approved, low
  risk, score 0.97), was installed/registered, and its Card was applied through the same review flow;
  searching for "design an AI-run rule-driven text adventure game without deck-building" gives top-1
  confidence 0.875;
- Probes: the last full run covered 159 checks — 154 pass / 3 fail / 2 pending; all 3 failures came
  from upstream `SKILL.md` files violating the Agent Skills spec (2 descriptions >1024, 1 non-ASCII
  name) and should not be "fixed" by relaxing lint; both built-in skills and the newly installed skill
  pass their probes;
- First real task (local skill smoke): search → invoke → checker → verify → probe → outcome
  passed; the human-edit minutes still await user confirmation, see
  the internal real-task record (not published);
- Installer: local directory/ZIP works; no script execution; the registry and drafts are refreshed
  automatically;
- Real adapters: Qwen and CodeBuddy completed real calls; the current Kimi subscription has no
  permission;
- Routing eval: dev top-1 94.1% / top-3 100%; held-out top-1 66.7% / top-3 73.3%; context reduction
  97.9–98.7% (against full injection, not against native lazy loading).

## Known limitations

- Lexical routing is still brittle for unseen phrasing; held-out top-1 is 66.7%, so do not report
  94.1% as generalization.
- Scanning only proves that "a directory looks like a project"; build files of vendored tools are
  filtered out, but monorepo sub-packages can still be treated as separate projects; drafts must be
  reviewed by a human.
- Capability tags on batch drafts are heuristic inferences; do not apply `needs_review` entries
  directly.
- The review policy is conservative by design: none of the 21 real drafts passed deterministically and
  automatically; `--fix` can repair examples, but the semantic confidence threshold of 0.75 remains
  the main bottleneck.
- Semantic re-checks are probabilistic judgements; after repair qwen repeatedly returned 0.72. They do
  not replace the deterministic gate and cannot upgrade high risk to auto. Do not lower thresholds to
  hit targets.
- Migration matching can produce ambiguous results; unmatched items are only written to
  `drafts/migrated/` and are never deleted or overwritten automatically; command/allow Cards are
  written as `migrated_unsafe`.
- Conversation mining (aggregating Cherry/Claude sessions) is not implemented in this round; it is
  planned for Phase 4 and off by default.
- A static probe only proves that installation/structure is valid, not task quality; the host's
  `probe-record` is needed to build behavioral evidence.
- The quality of `synthesize refine` depends on trace completeness and the local adapter; it only
  rewrites candidate text and the Card description/examples, it does not verify facts, and the warnings
  are "missing data hints", not conclusions. Refining still requires `approve` → `probe` → a real
  `outcome`.
- The built-in `capability-author` is a process constraint and a writing standard, not an automatic
  generator; without a recorded gap it will not start synthesizing.
- `synthesize approve --auto` only runs the deterministic review and never calls semantic; the
  capabilities/examples/permissions in a candidate Card are still self-reported, so the review only
  proves that they pass the rules, not that the task quality is good. Retrieval replay puts the
  candidate itself into the corpus, which allows self-matches; do not treat it as independent
  generalization evidence.
- `--auto` only installs/registers by default and does not write the overlay; `--auto --apply` is
  needed to apply the Card.
- `.caprouter/probes.jsonl` is append-only; `caprouter prune --probes --keep N` can rotate it, but
  nothing rotates automatically by default.
- `migrate remap` can point unmatched drafts at a local path and regenerate them, but they still need
  review/apply; the original drafts in `.remapped/` are archived only.
- Phase 2 conclusions are kept in internal notes; the public repository ships only synthetic
  benchmark templates and does not publish raw evaluation transcripts.
- There is no built-in LLM semantic acceptance; `unverifiable` must not be treated as a pass.
- The installer is offline by default; `update` / `versions` / `rollback --version` are supported;
  dependencies are only planned read-only by `deps plan/check`, which **do not run pip/npm**;
  signatures/trust roots are not implemented for now.
- The MCP server has 20 tools but is still a local stdio MVP: no streaming/progress/cancel/auth/
  concurrency, and commands have no OS-level sandbox.
- Host compatibility: the Claude Code plugin static validation and Cherry Studio MCP have been
  exercised; `setup` config generation for Claude Desktop / Cursor / VS Code is new (print by default,
  backup before `--write`); a cross-host behavior matrix is still not systematic.
- `mode: orchestrator` and `mode: bus` coexist; the documentation treats bus as authoritative.
- The 97.9% context reduction is measured against "full injection"; Claude Code's native skill bodies
  load lazily, so the real gain against native loading has not been verified.

## Security boundary

- No skill is installed over the network automatically; online content can only travel through
  `discover → fetch → quarantine → vendor → install --approve`, installation always needs human
  approval, and source and hash are written to the manifest. An uninitialised workspace returns
  `not_initialized` directly instead of writing a partial config.
- `discover` / `fetch` require an explicit `--allow-network` and only allow skills.sh; `fetch` only
  writes to quarantine, validates path, file count and total size, and does not execute the downloaded
  content; `vendor` only reads quarantine.
- Installation never executes scripts; runtime execution only happens in command mode of
  `capability invoke`, in command probes and in the `run --execute` / `host` adapters, and all of those
  require explicit authorization.
- Command invoke needs four conditions at once: `invoke.allow=true`, `bus.allow_execute=true`, the
  caller's `allow_execute=true` and the Card's `permissions.shell=true`. With `state` off, invoke and
  command probes are all refused.
- The working directory is only allowed inside the workspace or the Card directory and must already
  exist; `.caprouter/scratch` is used by default. A Card's `fs` must declare `read` (optionally
  `write`), and Cards without declared permissions cannot execute commands.
- `net` permissions are declarative: local execution has no OS-level sandbox and cannot stop a
  subprocess from reaching the network; use a container or a firewall when you need hard isolation.
  Without a sandbox, `fs` write scope cannot be fully enforced either — out-of-bounds access is
  limited only by the working-directory constraint and the audit trail.
- Execute/deny decisions are written to `audit.jsonl` (`CAPROUTER_AUDIT` overrides the path).
- ZIP installation refuses path traversal, symlinks, encrypted members and oversized files.
- In orchestrator mode the main agent can only write `.caprouter/plans/**` and `.caprouter/scratch/**`;
  in bus mode the host and the Card permissions constrain it.
- The guard only lets CapRouter's own commands through; `python -c`, redirection, `&`, command
  substitution, subshells and pseudo-script-name arguments are all rejected.
- Without `.caprouter/config.json` the hook does not intercept, so uninitialised projects are not
  broken.

## Complexity budget (stop rules)

- Core user commands: `init` / `scan` / `capability search|get|invoke|verify|probe|draft` / `install` /
  `state`;
- Ledger/lifecycle: `mcp-list` / `unmanaged` / `adopt` / `backup-skill` / `update` / `versions` /
  `uninstall` / `rollback`;
- Diagnostics/trust: `doctor` / `status` / `agents` / `audit` / `runs` / `backup` / `restore` /
  `installed` / `duplicates` / `health` / `lock` / `drift`;
- Maintenance/presentation: `setup` / `dashboard` / `prune` / `trash`;
- Host shells: Claude Code, Cherry Studio; `setup` also supports config generation for Claude Desktop /
  Cursor / VS Code;
- ≤3 CLI adapters (used only by the orchestrator compatibility path);
- `SKILL.md` ≤2KB; the registry stores metadata only; routing top-k ≤5;
- For every new component you must be able to say which old component it replaces, or which metric it
  improves.

## Relationship to demo-superagent-local

- Reuses its BM25 + Chinese bigram routing idea (this repository is an independent implementation and
  does not depend on the old project);
- Does not copy the 17k-line orchestrator, and does not copy the 24KB SKILL.md;
- The old project stays a read-only reference; CapRouter is an independently installable plugin.

## Tests

```bash
python -m unittest discover -s tests -v
python bin/caprouter.py doctor --workspace ./workspace
python bin/caprouter.py eval --workspace ./workspace
```

The suite in `tests/` (430 cases) is stdlib-only, needs no model or network, and is reproducible from any path:
every test creates its own temporary workspace and redirects `HOME`/`USERPROFILE`/`APPDATA`, so it
never touches the real profile. It covers pipeline acceptance/trust boundaries, project-nested skill
indexing, UTF-8 BOM tolerance, the guard write roots and executable trust, host link/unlink safety,
the MCP tool-surface contract and the CLI exit-code/`--workspace` contract.

Next priorities and kill gates: `docs/roadmap.md`; positioning, competitors and target users:
`docs/positioning-2026-11-23.md`.

## Contributing

See `CONTRIBUTING.md`; for security issues see `SECURITY.md`; for changes see `CHANGELOG.md`.

## Documentation

- Windows install guide: `docs/install-windows.md`
- Security hardening: `docs/security-hardening-2026-11-23.md`
- Roadmap and kill gates: `docs/roadmap.md`
- Positioning and competitive review: `docs/positioning-2026-11-23.md`
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security policy: [SECURITY.md](SECURITY.md)

## License

MIT, see `LICENSE`. CapRouter's own code is MIT; third-party skills fetched into quarantine keep their
own licenses, and the license detection done by `fetch` is only a text heuristic, not legal review.
