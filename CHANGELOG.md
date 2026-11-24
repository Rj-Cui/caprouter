# Changelog

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
