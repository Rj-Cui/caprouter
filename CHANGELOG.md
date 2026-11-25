# Changelog

## 0.7.2 — 2026-11-25

Fixes the findings of an external audit of 0.7.1: one command-execution path, one crash, one broken
core loop, plus the tests and documentation that would have caught them.

### Security

- **`pipeline verify/run/outcome` now only accept run directories under
  `<workspace>/.caprouter/pipeline-runs`.** Command acceptance runs `argv`, `check.allow` and
  `step.permissions.shell` taken from the run's own `plan.json`; before this, any directory holding a
  syntactically valid `plan.json` was accepted, so writing one into the guard's own writable area
  (`.caprouter/plans`) and calling `pipeline verify --allow-execute` was arbitrary command execution.
  Verified with a regression test and a reproduction of the original chain.
- **The guard honours `guard.write_allow`.** `create_workspace` always wrote it, but `_allowed_write`
  ignored it and hard-coded plans/scratch. Entries outside the workspace are ignored instead of
  silently widening the sandbox.
- **A `caprouter` executable resolved inside the writable area is rejected.** Trusting the basename
  meant a dropped `caprouter.cmd` in `.caprouter/plans` passed the shell allowlist.
- **The guard fails closed for execution-capable tools.** If it raises, `Write`/`Edit`/`Bash` are
  denied (and the reason is audited) instead of silently allowed; read-only tools stay usable.
- `guard.enforce_in_bus_mode` (default `false`) keeps the host-wide guard active in a `bus` workspace.
  Bus mode is documented as *no host-wide gating*, not as protection.
- `link_managed_skill` / `unlink_managed_skill` reject capability ids that are not a single path
  component (`skill:../x` could `rmtree` outside the host skill root) and verify the target stays
  inside the root.

### Fixed

- **`pipeline plan` crashed with `AttributeError: 'str' object has no attribute 'get'`** when a step
  used a free-text acceptance criterion — the same shape `capability verify --criterion` takes. It
  also left a half-initialized run directory behind. Rendering now handles both shapes and the run
  directory is written all-or-nothing.
- **Project-nested skills are indexed.** A repository usually keeps its skills in
  `<repo>/skills/<name>/SKILL.md` (or `.claude/skills/...`); before, only the project directory was
  registered, so `capability search` matched the repository name and abstained on the actual need.
  Nested skills are now registry `skill` entries with a `container` back-reference.
- **UTF-8 BOM tolerance** for every file CapRouter reads from the machine (requirements, package.json,
  pyproject, SKILL.md, plan JSON, host MCP configs). A BOM-prefixed `requirements.txt` used to produce
  a dependency literally named `\ufeffpypdf`, and a BOM-prefixed `package.json` was reported as
  "not a JSON object", which emptied a project Card.
- **MCP tool surface matches the bundled skill.** `core` now exposes the ten tools
  `skills/caprouter/SKILL.md` actually calls (search/get/invoke/probe/probe_record/verify/
  record_outcome/list_gaps/install/uninstall). It previously exposed eight *different* tools, so a
  host following the skill got `unknown tool` for probe, probe-record, outcome, gaps, discover and
  card-draft. The plugin `.mcp.json`, `setup --host`, the DSH bundle text and the docs now agree.
- **Windows console encoding**: the CLI no longer forces UTF-8 on a `cp936`/`cp950`/`cp932` console,
  which turned Chinese paths into mojibake. UTF-8 is only forced when the current codec cannot
  represent CJK at all.
- `capability draft --id` now writes the draft. It only printed it, so the documented
  generate → `review` → apply flow reviewed nothing.
- `audit` accepts `--workspace` and capability commands scope audit events to their workspace
  (`_capability_workspace` now calls `set_audit_workspace`).
- Empty listings (`agents`, `runs`, `backups`) print a short explanation instead of nothing.
- Exit codes follow one contract (documented at the top of `cli.py`): `0` success, `1` failed/invalid,
  `2` needs an explicit flag (`--approve`/`--yes`), `3` disabled. `trash --json` and `uninstall`
  previews returned `0` while their non-JSON/rollback siblings returned `2`; `pipeline outcome`
  returned `0` for an invalid run.

### Added

- **`setup --host deepseek-harness --portable`**: emits a config-only DSH bundle with **no path from
  the machine that produced it** — the interpreter is resolved when the row mounts
  (`CAPROUTER_PYTHON`, else `py` on Windows and `python3` elsewhere), the server starts as
  `python -m caprouter mcp`, and the workspace is resolved at startup instead of being baked in.
  The default (non-portable) form still embeds this machine's paths for people running from a
  checkout. `tests/test_dsh_bundle.py` asserts the portable bundle leaks nothing.
- **Offline and older-setuptools installs**: `build-system.requires` is `setuptools>=61` and the
  license uses the legacy table form, so `pip install .` works with the setuptools shipped next to
  older Python 3.11 installations (previously `setuptools>=77` required network access).

- **`tests/`** (+80 unittest cases on top of the existing 350, stdlib only, no model or network): pipeline acceptance and trust
  boundaries, project-nested skill indexing, BOM tolerance, guard write roots/executable trust/
  fail-closed, host link safety, MCP tool-surface contract, CLI exit-code and `--workspace` contract,
  and a surface test that parses and prints help for every registered command.
- **`eval/routing_cases.json`**: `caprouter eval` failed with "no cases found" because the file never
  shipped. The seed cases use only the two built-in skills and abstain correctly on unrelated needs.
- **`examples/pipeline.example.json`**: no pipeline ships built in; this is the shape
  `<workspace>/.caprouter/pipelines/<id>.json` expects.
- `caprouter/cli_common.py`: one workspace resolver, one JSON printer, one exit-code contract and the
  console-encoding policy, replacing the copies that lived in `cli.py`.
- **`release.ps1` packages what the docs tell people to run**: `tests/`, `eval/`, `examples/` and the
  plugin-root `.mcp.json` were missing from the archive (the first three were in the excluded-dir
  list), so a release ZIP could not run the documented test/eval commands and the Claude Code plugin
  root had no MCP registration. A release archive now contains 119 files and includes all four.

### Hardening (second pass)

- **ZIP extraction counts the bytes it actually writes.** The pre-check summed the sizes declared in
  the archive headers, which the archive controls; a bomb could declare small sizes and still expand.
  Both the per-member and the total budget are now enforced against the written stream.
- **Rollback cannot be pointed outside `installed/.trash`.** `uninstalled.jsonl` is a plain data file;
  a tampered row used to send `rollback` to any directory. The trash path is now validated, and
  `rollback --version` picks the newest copy by modification time instead of by directory name (old
  second-resolution names and unpadded millisecond suffixes did not sort chronologically).
- **Trash stamps are zero-padded milliseconds** (`YYYYmmdd-HHMMSS-mmm`) everywhere
  (`install`, `uninstall`, `rollback`, `adopt`), so same-second operations no longer collide and name
  order equals time order.
- **MCP tools reject unknown arguments.** A typo such as `top_k` used to be dropped silently and the
  tool answered with the default; the call now fails with the offending name and the allowed list.
- **`health` and `review` no longer contradict each other.** A Card whose review is `needs_review`,
  `rejected` or `repair_failed` is reported as `probation` instead of `active` just because a static
  probe passed; rows carry `review_status`.
- **`install --approve-hash <hash>`** pins the previewed source hash (mirrors the MCP `approve_hash`
  flow): the CLI used to treat `--approve` as approval of whatever was on disk at that moment.
- `doctor` no longer passes a missing `delivery` key silently (`Path("") == "."` made the check
  always true); it reports the missing key and keeps going.
- `routing.min_score` from the workspace config is actually applied to capability search; it was
  hard-coded to `0.0`.

### Documentation

- Corrected the test counts (`369`/`350`/`316`/`247` in four places, none reproducible) to the actual
  suite; removed the `node mods/.../register.test.mjs` instruction for a file that does not exist.
- Corrected the "8 core tools" claims to 10, the `init` layout (it creates `scratch/` and
  `deliverables/`, not `runs/`), the `~/.caprouter/audit.jsonl` path (it is per workspace) and the
  "built-in `fiction-deslop` pipeline" claim (it never shipped).
- `schemas/registry.schema.json` accepts the `mcp` entry type the scanner already produced.

## 0.7.1 — 2026-11-25

### Fixed

- **Prompt-mode content boundary (security)**: a Card's `content_path` — including one restored
  from an untrusted migration bundle — can no longer read files outside the capability directory and
  the workspace. Absolute paths, `..` traversal, directory listings and symlink escapes return
  `blocked`; `capability invoke --allow-external-content` / MCP `allow_external_content` is the only
  explicit escape hatch. Regression tests cover all four cases.
- **Migration rebinding**: imported cards no longer keep the source machine's `content_path`.
  Matched cards are rebound to the local capability; unmatched/unsafe drafts drop the path and must
  be regenerated during review.
- **Rollback truthfulness**: auto-install rollback now reports `removed`, `restored_previous`,
  `draft_removed`, `issues` and `ok`. A failed delete/unlink/restore is recorded instead of being
  reported as done.
- **Config read errors**: `read_json_checked` distinguishes `missing` / `invalid` / `io_error`; the
  bus config path uses it instead of collapsing all three into an empty dict.

### Changed

- **Dependency cycles removed**: probe summary/history moved into `probe_summary` and
  `probe_log` (previously `capabilities` ↔ `probes`), and ZIP/license helpers moved into
  `archive` / `licenses` (previously `install` ↔ `remote`). The AST import graph now has no
  non-trivial SCC.
- Removed unused constants `DENY_TOOLS`, `RISK_ORDER`, and an unused local in `route`.
- Moved the internal mod UI plan out of the public repository.
- 369 unit tests (19 new: trust boundaries, rollback truth, JSON error states).

## 0.7.0 — 2026-11-24

**Host awareness + DeepSeek Harness support + one-click install + uninstall integration + release packaging.**

### Added

- **Windows one-click install**: `install.ps1` + `install.bat`. Finds Python 3.11+ automatically, copies the
  program to `%LOCALAPPDATA%\CapRouter`, generates the `caprouter` command, creates a workspace and runs
  `init` plus a search demo; no pip and no network needed; prints a `winget` hint when Python is not found.
- **DeepSeek Harness host**: `setup --host deepseek-harness`. `--write` generates a config-only bundle that
  can be added directly (`package.json` + `cordis.patch.yml` + English and Chinese locales + icon), and
  `--link-skills` syncs `.caprouter/installed/skills/*` to `~/.dsh/skills`
  (existing entries are skipped by default, `--force` overwrites, `--skills-root` changes the root).
- **Host awareness** (`caprouter/hostaware.py`): `mcp --host <id>` makes search results carry
  `host.loadable` / `health.loadable_by_host` / `health.host_hint`; when the selected capability belongs to
  another host it returns `host_warning`. `capability_install` gains `link_host` (true by default), so a
  successful install copies the skill into the current host's skill root and the host can load it directly.
- **Tool surface tiers**: `mcp --tools core|full`. Default is `full` (20 tools, backward compatible); the DSH
  bundle explicitly uses `core` (8 tools: search/get/invoke/verify/install/uninstall/health/duplicates) to
  lower the per-request token cost.
- Added `tests/test_setup_dsh.py` (8 tests) and `tests/test_host_aware.py` (21 tests).
- `release.ps1`: builds `dist\caprouter-<version>.zip` + `.sha256` + release notes.
- **Uninstall integration**: `capability_uninstall` gains `unlink_host` (true by default), `force` and
  `host_root`; after a successful uninstall it safely removes the host copy based on the content hash
  recorded before uninstall (a manually modified copy is kept and reported, `force=true` removes it);
  the CLI equivalent is `uninstall --host <id> --unlink-host`.
- **Search favors the current host**: `capability_search` gains `prefer_host` (true by default); cross-host
  candidates are down-weighted and loadable candidates come first; when it selects a different capability it
  reports `selection_adjusted` / `selected_original`, and only when no loadable candidate exists does it
  keep the original selection and return `host_warning`.
- **Managed and linked counts as loadable**: new `managed_linked` state - managed skills attached to a host
  through `link_host` / `--link-skills` now report `loadable_by_host=true` during search (previously they
  were misjudged as not loadable).
- `setup` configurations for other hosts now also write `--tools core --host <host>`;
  `setup --host-root` is an alias for `--skills-root`.

### Changed

- Generated DSH bundles no longer write `cwd`, so the host process does not treat the install directory as
  the working directory and lock upgrades.
- The CLI and the MCP server now write audit events to `<workspace>/.caprouter/audit.jsonl`
  (`set_audit_workspace`) instead of piling every workspace and test event onto `~/.caprouter/audit.jsonl`.
- **English landing page**: README now opens with an English title + one-paragraph intro + Supported hosts
  matrix in `README.en.md`; the English landing page is now the primary README.
- **Host boundaries spelled out**: 8 hosts support loadability decisions (DSH / Claude Code / Cherry /
  Cursor / Codex / Gemini / VS Code; Claude Desktop is config-only) and 6 hosts have `setup --host`
  support; verified with DSH, the Claude Code plugin form and Cherry Studio MCP.
- **Public-release hygiene**: raw Phase 2 evaluation transcripts (`eval/phase2/`) moved to a private
  archive (published benchmark cases stay in `eval/*.json`), git history squashed to a single release
  commit with a GitHub noreply author, and README badges added.
- **Public-repo cleanup**: internal assessment/handoff/status/decision documents moved out of the
  repository, `eval/*.json` replaced with synthetic `skill:demo-*` templates, absolute project paths
  genericized to `<repo>`, and the sample pipeline uses demo capability ids.

### Fixed

- Tests no longer hard-code the `D:/CapRouter` path (`tests/test_guard.py`), so
  `python -m unittest discover -s tests` is reproducible after copying the repository anywhere.
- The installer no longer fails outright when the old `app` directory is held by a host: it uses
  `app-<timestamp>` and keeps the two most recent copies; after upgrading, remove the old plugin and add
  the new one in the host.
- README now documents Windows one-click install, DSH integration, `--link-skills` and the core 8 tools;
  the `--host` list includes DSH.

## 0.6.3 — 2026-11-23

### Added

- **MCP inventory**: `mcp-list` and registry `type: mcp`; reads Claude/Cherry/Cursor/Codex/Gemini/
  VS Code configs; env values are redacted.
- **Host roots**: `.agents`, `.codex`, `.gemini`, `.cursor`, `.github`, project-local skills,
  macOS Cherry; `--skill-root` / `--plugin-root`.
- **External lifecycle**: `unmanaged`, `adopt` (copy-only), `backup-skill`.
- **Batch Cards**: `capability draft --all --type skill|plugin`, `bootstrap --draft-registry`.
- **Update/versions**: `update`, `versions`, `rollback --version/--hash`.
- **Diagnostics**: `duplicates` (with `--prefer <path>` to choose the winning root), `health`
  (draft/probation/active/degraded/retired, `--probe` to run static probes first), `lock`, `drift`,
  static HTML `dashboard`.
- **Host setup**: `setup --host claude-desktop|cursor|vscode|claude-code|cherry` (print by default).
- **Maintenance**: `prune` for JSONL rotation, `trash` list/clean.
- **Dependencies (plan-only)**: `deps plan` / `deps check` read package.json, pyproject,
  requirements and lockfiles; produce exact commands, risk flags and missing-package reports.
  **No package manager is executed.** MCP `capability_deps` exposes the same read-only view.
- **Signature decision**: cryptographic signatures/trust roots are intentionally not implemented;
  the existing hash chain remains. Rationale and probability bands are recorded in an internal
  decision note (not published).
- **Remote**: discover description/license when available, offline cache, `fetch --url` with an
  allowlist, `vendor --dir`.
- **Pipeline**: `pipeline run` executes command-mode steps; prompt steps stop at `awaiting_host`.
- Online discovery: skills.sh `discover`, quarantine `fetch`, `vendor`, `quarantine list/clean`.
- `pipeline plan/verify/outcome` with the built-in `fiction-deslop` pipeline.
- `uninstall` / `rollback` with preview-by-default and trash-based removal.
- `synthesize refine` and the built-in `capability-author` skill.
- MCP tools: 20 total, including `capability_discover`, `capability_install`, `capability_uninstall`,
  `capability_deps`,
  `capability_versions`, `capability_health`, `capability_duplicates`, `capability_lock`,
  `capability_drift`.
- Packaging: `pyproject.toml`, MIT `LICENSE`, `python -m caprouter`, `--version`,
  publish workflow, Homebrew formula template, binary notes.
- English overview (`README.en.md`), `SECURITY.md`, `CONTRIBUTING.md`.

### Security

- `state` gating for invoke, command probes and adapters.
- Card permission checks; four-condition command execution.
- `guard.py` rewritten with argv parsing and quote-aware dangerous-syntax detection.
- `discover` / `fetch` reject redirects that leave the skills.sh allowlist.
- Install previews are hash-pinned; a changed source returns `source_changed` unless `--force`.
- `provenance.content_hash` is verified before install; mismatch blocks unless `--skip-provenance`.
- Local installs refuse symlinks by default (`--allow-symlinks` to copy them as links).
- `fetch --url` enforces an https host allowlist and a 50 MB cap; redirects are re-checked.
- MCP `capability_install` requires `approve_hash` from a preview, not a bare `approve=true`.
- `install` returns `not_initialized` instead of writing a partial config.
- Review scope fixed for both draft locations; unreachable apply no longer counts as applied.
- Backup `--label` sanitized against traversal.
- Same-second `refine` / `improve` collision fixed.

### Changed

- Default skill/plugin roots are portable; private `D:` roots removed.
- Tray and slash-command workspace defaults derive from the repo, not `D:/CapRouter`.
- GitHub Actions test matrix: Linux/macOS/Windows, Python 3.11/3.13.
- `doctor` reports `mode=package` when running from a pip install instead of warning about
  missing plugin assets.
- `bootstrap` warns when the OS has no default scan roots.

### Fixed

- `init` no longer raises `EOFError` in non-interactive environments (CI, hosts, piped stdin).
- `skills/caprouter/SKILL.md` trimmed to 1969 bytes, within the 2048-byte contract.
