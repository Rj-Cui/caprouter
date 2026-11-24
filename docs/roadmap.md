# CapRouter Next Steps (0.6.x)

## 0.6.3 actual progress (2026-11-23)

- Security hardening (P0/P2) + install integrity (preview hash, provenance, license/deps, symlink, journal);
  316 tests; doctor problems 0 / stale 0;
- MCP listing and host roots: `mcp-list`, `type: mcp`, `.agents`/`.cursor`/`.codex`/`.gemini`/`.github`;
- External skill lifecycle: `unmanaged` / `adopt` / `backup-skill`;
- Batch Cards, `update` / `versions` / `rollback --version`;
- Diagnostics: `duplicates` / `health` / `lock` / `drift` / `dashboard`;
- Host configuration `setup`, maintenance `prune` / `trash`;
- Dependency plan `deps plan/check` (read-only, does not execute pip/npm);
- Online discovery: skills.sh `discover` (description/license, offline cache), quarantine `fetch`,
  `vendor`, `fetch --url` (allowed domains), `vendor --dir`;
- pipeline: `plan/run/verify/outcome`; command steps execute, prompt steps `awaiting_host`;
- MCP: 20 `capability_*` tools;
- Engineering/packaging: git initialization, MIT LICENSE, `pyproject.toml`, CI, PyPI/Homebrew/binary templates;
- **Goal change**: switched to open source, first users are heavy multi-host users; for the positioning and new kill gate see
  the internal open-source decision (not published).

Still not done (higher priority than adding features):

1. 10–20 real repetitive-task baseline runs;
2. Second real-machine migration;
3. Semantic confidence calibration;
4. MCP progress/cancel/auth/concurrency and command sandbox;
5. Isolated dependency install (Option C); signatures/trust root were decided to be skipped for now; verification with real external users.

## 0.6.1 done / still missing (2026-11-21)

Done: medium apply gate, risk model fix, `review --fix`, semantic cache, semantic tool isolation,
migration content hash and unsafe Card protection, disk-scan vendored tool noise reduction, Gradle/Maven/CMake evidence.

Still missing (by priority):

1. Real task baseline (`human_edit_minutes`);
2. Semantic confidence calibration after repair (qwen currently gives 0.72 several times, below 0.75);
3. `drafts/migrated/` remap/review closed loop;
4. Real second-machine migration rehearsal;
5. Continue/stop decision record for the Phase 2 kill gate;
6. Conversation mining Phase 4.

## Current baseline (2026-11-21)

- 157 Python tests, mod tests, plugin static validation pass;
- bus mode usable: search / get / invoke / verify / probe / outcome / gaps;
- local installer usable: directory/ZIP + `--approve`, does not execute scripts;
- Card draft generator usable: deterministic generation, not stored in the registry by default;
- probes usable: static ones run automatically, behavior is backfilled by the host;
- disk-scan pipeline usable: `scan-projects` / `bootstrap` / `capability draft --all`;
  skips system drives and system/cache directories by default, with a budget and truncated report;
- the first real task (local skill smoke) passed deterministic acceptance and probe backfill:
  the internal real-task record (not published);
- still unproven: **whether manual edit minutes decrease, token cost, A/B baseline**.

## 0.6.0 done: evidence, review, semantic re-check, migration

- `evidence.py`: read-only extraction of scripts/entrypoints/README commands;
- `review.py`: deterministic review + retrieval replay + risk grading + tiered automatic apply;
- `semantic.py`: built-in rubric + optional local skill rubric + CLI agent JSON re-check;
- `migrate.py`: export/import bundle; applies automatically on a successful match, writes drafts when unmatched;
- `bootstrap` reviews by default, `--no-review` for a new machine after migration;
- all 22 real drafts are needs_review/medium, 0 automatic passes; semantic re-check can point out real issues.

**The next step prioritizes "draft quality" over loosening review**: insufficient examples and over-inferred capability tags
cause a low automatic review pass rate. Automatically repairing/rewriting drafts (semantic suggest_fixes -> generate a new draft ->
review again) is safer than lowering the threshold.

## 0.5.0 done: disk scan and bootstrap

- `caprouter/scan.py`: `classify_drives`, `resolve_scan_roots`, `scan_project_roots`,
  `summarize_scan`; metadata only, per-root budget, truncated report;
- `caprouter/workspace.py`: `init` and `bootstrap` share initialization logic;
- `caprouter/cards.py`: `generate_project_drafts`; deterministic project capability inference;
  skips unchanged drafts;
- CLI: `scan-projects`, `bootstrap`, `capability draft --all`;
- fix: overlay metadata (description/capabilities/keywords/domain) participates in routing;
- de-duplication: `split_keywords` / `default_invoke` / `infer_domain_from_text` /
  workspace initialization logic kept in only one copy.

**The next step is still P0**: prove with real tasks that manual edit minutes decrease; the disk-scan pipeline only lowers loading cost,
it does not equal proof that the capabilities are usable.

## P0: one real task baseline (1 week)

**Goal**: prove that the bus reduces manual editing on real tasks, rather than only adding tools.

Pick 1 host (Claude Code or Cherry) + 1 repetitive task (e.g. PDF→Word, xlsx extraction, research report), and run it 10–20 times:

Record:

- first-pass success rate;
- probe pass rate (static / behavioral);
- routing top-1 / abstain;
- manual edit minutes;
- reuse rate (the same capability being selected again);
- failure modes and gaps.

Kill gate:

- manual edit minutes do not decrease;
- reuse rate does not increase;
- attribution remains untrustworthy (cannot say which capability contributed what).

If any holds → stop adding installations/automatically writing Cards and return to single-point capability polishing.

## P1: probe evidence closed loop (1 week)

- Add 3–5 host probe tasks for the Top 20 capabilities;
- Backfill `probe-record` with real task results to form behavioral evidence;
- Introduce domain shards (start with only 2–3: pdf_to_docx, xlsx_extract, market_research);
- Only update `domain_profiles` after 3 consecutive consistent signals; one strong failure + manual confirmation can demote immediately;
- Card lifecycle: `draft → probation → active → degraded → retired`;
- Add rotation/compaction to `probes.jsonl` (currently append-only, 137 probe lines in total).

Acceptance:

- ≥60% of the Top 20 capabilities have behavioral evidence;
- capabilities with failed probes no longer enter high-confidence routing;
- probe latency and cost are acceptable (< 60s per capability, no LLM by default).

## P2: installer hardening (1 week)

- [x] Add `uninstall` / `rollback` (preview by default, `--yes` executes, restore Card);
- Confirm the source directory hash matches the approved one before install (tamper protection between preview → approve);
- manifest adds dependencies / license / minimum_caprouter_version;
- Support a stable schema for `install --dry-run --json`;
- [x] Online discovery: skills.sh read-only search + quarantine download + vendor six-step breakdown; no automatic install/execute, manual approval required.
- [x] fetch recognizes common LICENSE names (heuristic, does not replace legal review).
- [x] Client-side pagination (first 100), one retry and request interval.
- [ ] Signature verification of online sources.

Acceptance:

- install, overwrite, rollback and uninstall all have tests;
- no failure path breaks an existing installation;
- there is no silent execution path.

## P3: routing quality (1–2 weeks)

- Expand held-out cases (current top-1 66.7%, do not report dev 94.1% as generalization);
- Do card-level held-out evaluation first, then consider embedding / LLM rerank;
- Introduce domain profile as a lightweight feature;
- Keep abstain first: prefer abstain over forcing a wrong capability.

Acceptance:

- held-out top-1 ≥ 80%, or fallback success rate after abstain ≥ 70%;
- routing latency p95 < 100ms (pure lexical path).

## P4: MCP and host compatibility (1 week)

- Support progress / cancel / concurrent requests;
- Verify at least 1 more host (Cherry or a real Claude Code session);
- Record the host compatibility matrix (initialize / tools/list / tools/call / error handling);
- Add an OS-level sandbox to command execution or clearly warn "no sandbox".

## Do not do

- Silent install + execute unknown plugins;
- Claim "capability usable" without a probe;
- Claim "trustworthy" without a snapshot/claim;
- Use "multi-agent" as a selling point;
- Build UI/marketplace big projects first.

## One sentence

**0.4.0 already has the skeleton "installable, findable, verifiable"; the next step is not more features, but proving with one real repetitive task that "manual edit minutes really decrease".**
