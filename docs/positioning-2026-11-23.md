# CapRouter Positioning and Competitor Assessment (2026-11-23)

## 0.1 Implementation status (evening of 2026-11-23)

Most of the "not yet done" items listed in section 3 of this document have been added to the code: MCP listing, host roots, adopt/backup, install integrity,
batch Cards, update/versions, duplicates/health/lock/drift, setup, discover cache and URL sources,
pipeline run, dashboard/prune/trash, deps plan/check.

Signatures/trust root have been decided to be skipped for now (decision recorded in internal notes,
not published); dependencies adopt
Option B (generate a plan only, do not execute pip/npm). Still not done: real repetitive-task baseline, second real machine,
isolated dependency install, external user verification.

## 0. Conclusion first

1. **The positioning needs narrowing, but the name does not need changing.** The current docs still use "router/bus" as the subject;
   routing has been commoditized by host-native tool search.
2. **Recommended positioning: multi-host local capability ledger + evidence/lifecycle layer.**
   In one sentence: *know what is installed, prove it still works, and carry it safely to another machine.*
3. **First users: heavy multi-host users** (Claude Code + Cherry Studio + Cursor/Codex,
   with 30–200 skills/plugins/MCP on one machine). Not beginners, not enterprise compliance buyers.
4. **What not to do:** no self-built marketplace/registry; no model/provider gateway; no general multi-agent orchestration;
   do not compare recall with native tool search; do not claim static scanning guarantees security.

---

## 1. Basis for judgment (verified facts)

### 1.1 Discovery and installation are already commoditized

- skills.sh grew in seven months to **about 1 million skills and nearly 280 million installs**
  (Vercel, State of agent skills: https://vercel.com/blog/state-of-agent-skills).
- The MCP official Registry lists **69,150** servers; 47,355 have remote endpoints,
  27,349 (58%) can handshake without an account, 14,452 (31%) require login, and **5,159 (11%) are unreachable**
  (Agents Index, 2026-11-23: https://agentsindex.org/reports/state-of-mcp/).
  Another measurement on 2026-11-16 found 65,580 entries, 54% account-free handshake, 10% no response
  (Stork: https://www.stork.ai/blog/mcp-registry-endpoints-checked-november-2026).
- Smithery's MCP Proxy and hosted identity were **shut down on 2026-10-31**, keeping only the directory,
  with Arcade taking over (https://www.smithery.ai/future).

**Implication:** "discovery/install/directory" is not CapRouter's battlefield; a self-built marketplace cannot beat the scale,
and would also bear review and abuse-handling costs.

### 1.2 Selection has been absorbed by host-native features

- Claude tool search: in multi-server scenarios, about **55k tokens** of tool definitions, search usually reduces it by **>85%**,
  loading only 3–5 tools; selection accuracy drops after **30–50 tools**
  (Anthropic: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool).
- OpenAI also provides hosted/client tool search (Responses API).

**Implication:** CapRouter's lexical routing held-out top-1 66.7% has no platform advantage;
routing can only be a supplementary "local, offline, cross-host" capability, not the core selling point.

### 1.3 Security/audit needs are real, but static scanning is not trustworthy

- Snyk re-scanned 10,000 skills each on skills.sh and ClawHub: **>64% have exploitable issues**;
  critical 7/11, high 339/930, secrets detected 1/16
  (https://research.snyk.io/blog/skill-analysis/).
- Trail of Bits bypassed ClawHub, Cisco's scanner and all three scanners integrated in skills.sh
  with 3 of the 4 malicious skills it built within an hour
  (2026-06-03: https://blog.trailofbits.com/2026/06/03/the-sorry-state-of-skill-distribution/).
- Zenity disclosed at Black Hat: a malicious skill family spread through skills.sh accumulated
  **1.7 million installs** (not unique users), and dozens of other malicious/dangerous skills were found
  (2026-08-06: https://www.businesswire.com/news/home/20260806707467/en/).

**Implication:** users need "evidence and lifecycle", but any promise of "we scanned it so it is safe" will be broken
by results like Trail of Bits'. CapRouter should position itself as **isolation + manual approval + reviewable evidence**,
not a "security scanner".

### 1.4 Close competitors have appeared (2026)

Star counts as of 2026-11-23 from Shields.io; features from their websites/docs.

| Project | Stars | What it does | Overlap with CapRouter |
|---|---:|---|---|
| **Skills Manager** | 7.7k | Desktop + CLI; skill library for 56 agents, presets, private Git sync, symlink/copy, refuses to overwrite conflicts | High: multi-host sync/library |
| **Skillporter** | — (new) | v3.2.0; 19 harness adapters (8 runtime-verified), lockfile, validate/scan, journal, export/restore, rollback; Python 3.9+, zero deps, MIT | **Highest**: sync + lockfile + rollback |
| **Capa** | 684 | `capabilities.yaml` installed into 35–39 agents; MCP gateway; SHA-256 adapter approval | High: declarative cross-host install |
| **LazySkills** | 340 | TUI; 70 agents; visibility matrix; bad-install/orphan-lock detection | Medium-high: multi-host visibility |
| **Dalo** | 3 | Git-backed team skill store, exact commit lockfile, drift preview, per-skill approval, uninstall | High: team lockfile/approval |
| **SkillDoctor** | 3 | Skill validity/security/conflict/token-cost audit, 0–100 score, quarantine/restore, GitHub Action | High: audit + CI |
| **Credenzo** | 7 | 11 clients; MCP + skills + plugins + instruction files; `lock --connect` summarizes tool definitions | Medium-high: audit/lock |
| **Ctx** | 593 | Recommends bundles from a 91k skill graph by repo/task | Medium: recommendation/selection |
| **CC Switch** | 157k | Provider switching + MCP/Skills/Prompts sync across 16 tools | Medium: cross-tool configuration |
| **MetaMCP** | 2.8k | MCP aggregator/gateway/namespaces | Medium: runtime aggregation |
| **Claude Code plugins** | — | Official/community marketplace; plugin = skills + agents + hooks + MCP | High: distribution and trust UI |

**Implication:** "multi-host skill library/sync" is no longer a blank space (Skills Manager 7.7k, Skillporter very close in features);
"audit/scoring" also has several new 2026 projects. CapRouter's remaining space is not "another synchronizer" or
"another scanner", but **making sync/install/migration an evidenced, reviewable, rollback-able process**.

---

## 2. Competitor layers and CapRouter's place

| Layer | Representatives | Strengths | Pitfalls | CapRouter's relationship |
|---|---|---|---|---|
| Host-native selection | Claude/OpenAI tool search | Platform-level, >85% token reduction, 3–5 tools | Host lock-in; no local ledger/migration | **Do not compete**; routing is only an offline/cross-host supplement |
| Discovery/distribution | skills.sh, Claude marketplace, MCP Registry, Glama/mcp.so/PulseMCP | Scale, install, ranking | Directory rot (11% unreachable), review burden, supply-chain risk | **Discover only, do not build**; quarantine then manual approval |
| Gateway/control plane | MetaMCP, Docker MCP Toolkit, CC Switch, Claude Code Router | Aggregation, provider switching, isolation | Complex configuration; does not prove capability quality | **Do not do** |
| Security scanning | Snyk, Cisco, SkillDoctor, SkillOx, Credenzo | Rules, scoring, CI | Static scanning can be bypassed (Trail of Bits); false positives | **Do not position as a "security scanner"**; do evidence/lifecycle |
| Multi-host library/sync | Skills Manager, Skillporter, Capa, LazySkills, Dalo | Cross-agent install, lockfile, conflict handling | Sync is not verification; no behavioral evidence | **Adjacent but not overlapping**; CapRouter adds "prove usable + migration audit" |
| Local capability ledger/evidence | **CapRouter** | Scan the local machine, Card, deterministic verify, probe/outcome, quarantine, migrate | Value unproven; few users; weak UI | **Core** |

---

## 3. Avoidable pitfalls (with evidence)

1. **Do not build your own market/registry.** The Smithery proxy shut down, MCP Registry is 11% unreachable,
   skills.sh already has 1 million skills. Building your own means bearing unlimited review and abuse costs.
2. **Do not compare recall with native tool search.** Claude already reduces 55k tokens to 3–5 tools;
   CapRouter's 66.7% held-out top-1 is not an advantage.
3. **Do not promise "safe because scanned".** Trail of Bits bypassed mainstream scanners within an hour;
   Snyk still found 64% with issues. The stronger the promise, the bigger the backlash.
4. **Do not add automatic install/automatic execution for convenience.** Remote content is arbitrary code execution;
   quarantine + `install --approve` + four-layer command gating is the right direction; do not give it up.
5. **Do not bundle third-party skills into the wheel/sdist.** Many have unclear licenses;
   keep fetching them at runtime into quarantine, and add `THIRD_PARTY_NOTICES` when necessary.
6. **Do not be Windows-only.** Default roots, tray and slash command have been made portable;
   the CI matrix now includes Linux/macOS/Windows.
7. **Do not expand features before proving value.** The lesson of the Phase 2 gate; do the real repetitive-task baseline first.
8. **Do not rely on a single host's hook/API.** Platforms change silently; CLI + MCP is the stable surface.
9. **Do not trade lower review thresholds for automatic pass rate.** The project already has this principle; keep it.
10. **Do not hide security boundaries.** `net`/`fs` have no OS sandbox, no signatures, MCP has no auth;
    `SECURITY.md` already states this; keep disclosing honestly.

---

## 4. Borrowable strengths

| Source | Borrowable |
|---|---|
| **Skillporter** | Zero-install demo via `uvx skillporter demo`; lockfile + journal; 19 harness adapters; shared-path dedup; export/restore; MIT + zero deps |
| **Skills Manager** | 56-agent matrix; per-agent toggles; refuses to overwrite unmanaged directories; private Git sync; no telemetry |
| **Capa** | A single `capabilities.yaml`; `capa wrap` shadow workspace; adapter SHA-256 approval |
| **SkillDoctor / SkillOx** | 0–100 score + finding codes; GitHub Action; quarantine/restore |
| **Dalo** | Exact commit lockfile; drift preview; per-skill approval; Sigstore/cosign direction |
| **Context7 / FastMCP** | Per-host install buttons/badges; examples-first README; 5-minute quickstart; clear non-goals |
| **Snyk / Zenity / Trail of Bits** | Speak with reproducible research/evidence, not "we are secure" |

---

## 5. Positioning judgment

### 5.1 Current problems

- The first sentence of the README is "local capability index + two-stage routing + auditable acceptance + executable probes";
  the subject is **routing**; but routing has been absorbed by host-native features.
- The "Router" in the name `CapRouter` makes people think "tool/model routing",
  easily confused with CC Switch/Claude Code Router.
- What is truly scarce among the existing assets is: **local scanning + Card + deterministic acceptance + probe/outcome +
  quarantine + migration**, not top-k routing.

### 5.2 Recommended positioning

> **CapRouter is the local capability ledger and evidence layer for multi-host AI agents.**
> It records what is actually installed on this machine, proves with deterministic checks that it still works,
> and carries audited capabilities safely when switching machines/hosts.

- **Headline (external)**: *Know what's installed. Prove it still works. Move it safely.*
- **Proof layer (internal/technical users)**: Card, deterministic verify, probe/outcome,
  quarantine, migration audit.
- **Routing**: a supporting capability; useful for offline/cross-host scenarios, but not the core narrative.
- **Name**: keep `CapRouter`; use the tagline to redefine "Router" as sending tasks to **verified** capabilities,
  rather than "retrieval among thousands of tools".

### 5.3 Target users

**First priority: heavy multi-host users**

- Use 2+ hosts at the same time (Claude Code, Cherry Studio, Cursor, Codex, Gemini CLI).
- 30–200 skills/plugins/MCP on one machine; duplicates, same name with different content, drift across machines.
- Pain point ranking:
  1. "What is actually installed, and which copy is in effect?"
  2. "Will it still work after an upgrade/switch of machines?"
  3. "How do I delete/migrate safely instead of breaking the configuration?"
- They are willing to use the CLI and can accept `pipx install` / `uvx`; they do not need a GUI to get started.

**Second priority: skill/plugin authors**

- Need pre-release self-checks: SKILL.md specification, Card, license, probe evidence.
- It is a **distribution lever**: authors attach a CapRouter evidence package, and users discover CapRouter.
- Need GitHub Action/CI integration; not done yet.

**Third priority: 2–10 person small teams**

- Need a shared ledger, drift alerts, audit export.
- Need lockfile/private Git/RBAC; currently only single-machine migrate, which is not enough.

**Not current users:**

- Enterprise compliance/SSO/RBAC buyers (no auth/team features);
- Users who only want to browse/install skills (skills.sh is enough);
- Model/provider routing users (CC Switch/Claude Code Router);
- Teams needing general multi-agent orchestration (LangGraph/CrewAI/native subagents).

### 5.4 Elevator pitch

> If you use Claude Code, Cursor and Cherry Studio at the same time, and have dozens of skills installed on one machine,
> CapRouter gives you a **reviewable local capability ledger**: it tells you which copies are in effect, which are already broken,
> and which can be carried safely to another machine. It sells no marketplace, builds no gateway, and does not pretend to replace the host's tool search.

---

## 6. Development paths

| Path | Content | Fits when | Risk |
|---|---|---|---|
| **A. Evidence/ledger layer (recommended)** | Multi-host inventory + verify + probe/outcome + quarantine + migrate; headline is "verified inventory" | You keep maintaining it and are willing to do a real task baseline | User education cost; needs a 5-minute onboarding |
| **B. Author CI validator** | GitHub Action: SKILL.md lint + Card + license + probe; evidence badge | Want to leverage author distribution | False-positive rate; compatibility with skills.sh/official formats |
| **C. Narrow-domain routing + evidence** | Only do 1–2 domains like pdf→docx, xlsx extraction, research; domain profiles + benchmarks | Have a high-frequency repetitive task | Back to comparison with native tool search |
| **D. Team governance** | Lockfile + audit export + RBAC + SSO | Have design partners/budget | Large engineering effort; currently no auth |
| **E. Archive/personal tool** | Keep scan/Card/verify/migrate, stop expanding features | A/B gates do not pass | Give up open-source growth |

**Recommendation: A as the product, B as the distribution wedge, C as a later proof point; D deferred, E is the stop-loss.**

Reasons:

- A uses the existing hardest-to-copy assets (deterministic verify, probe/outcome, quarantine, migrate);
- B does not need a self-built marketplace and borrows author distribution;
- C only when there is a real high-frequency task; otherwise it falls back into the "routing accuracy" race;
- D needs enterprise capabilities and does not match the current team/project stage.

---

## 7. Material/information each path needs to clarify

### A (evidence/ledger)

1. Interviews with 5–10 multi-host users: how they manage now, what they fear most, what they would install.
2. A 10–20 run baseline of one real repetitive task: first-pass success rate, probe pass rate,
   top-1/abstain, `human_edit_minutes`, reuse rate, failure modes.
3. Native baseline comparison: compare the same task against Claude tool search / native skill lazy loading for token, time, quality.
4. Definition of "verified": which checks count as evidence, which are only self-reported; signature/provenance roadmap.
5. Cross-platform CI results: scan/doctor/install measured on Linux/macOS/Windows.
6. Distribution: GitHub URL, PyPI release, Claude plugin directory, possibly Homebrew.

### B (author CI)

1. Interviews with target authors: how they self-check now, whether they would use an Action.
2. Compatibility with the Agent Skills specification/skills.sh submission format.
3. False-positive rate of lint/review and quality of fix suggestions.
4. The Action's permission model (read-only, no network, no code upload).

### C (narrow-domain routing)

1. 20+ in-domain task cases and acceptance criteria.
2. Comparison with native tool search/manual selection.
3. Latency p95, offline availability, domain profile maintenance cost.

### D (team)

1. 3–5 design partners; minimal requirements for RBAC/SSO/audit export.
2. Conflict and key management for private Git/lockfile.
3. Compliance/data-residency requirements.

---

## 8. Presentation, approach and format

- **README**: one-sentence positioning → 5-minute quickstart → per-host install buttons/badges →
  "What it is not" → compatibility matrix → known limitations. English first, with a link to Chinese.
- **Demo**: 60-second asciinema/GIF: scan → find duplicate copies → search → verify → migrate.
- **Evidence package**: each capability outputs JSON + Markdown, diffable, attachable to PRs/issues.
- **Security**: quarantine + manual approval + audit.jsonl + SECURITY.md; do not promise "scanned = safe".
- **Release**: SemVer, CHANGELOG, PyPI Trusted Publishing, GitHub Releases.
- **Community**: issue templates, `good first issue`, monthly changelog; do not operate a marketplace.
- **Docs**: README + docs/ first; consider a docs site after 10 external users.

---

## 9. Derivative items

- `caprouter lock`: lockfile + hash of installed capabilities (aligned with Skillporter/Dalo).
- `caprouter diff`: drift between two machines/two hosts.
- GitHub Action: validate skills/plugins in CI (aligned with SkillDoctor).
- `caprouter export --manifest`: a portable manifest for other hosts (aligned with Capa).
- Evidence badge: authors can show "CapRouter verified".
- Offline bundle: migration for air-gapped environments.
- VS Code/Cursor extension: ledger panel (do last).
- Team panel: health, drift alerts (path D).
- Domain profiles: pdf/xlsx/research (path C).
- Capability lifecycle: draft → probation → active → degraded → retired.

---

## 10. 30/60/90 day recommendations

**30 days: prove it works**

- Complete a 10–20 run baseline of one real repetitive task;
- Fix all cross-platform CI red lights;
- Publish GitHub + PyPI (`caprouter` name confirmed available);
- Rewrite the README with the "ledger/evidence" narrative.

**60 days: prove someone uses it**

- 5–10 external users complete `init → scan → search → verify`;
- Collect 3 real migration cases;
- Publish 1 GitHub Action (path B wedge).

**90 days: decide continue/narrow/archive**

- Against the new kill gate:
  10 external installs, 3 users complete the full flow, 1 repetitive task shows reduced manual editing.
- Not met → archive per E or shrink back to a personal tool; met → then do domain profiles/team capabilities.

---

## 11. Sources (by theme)

**Scale/discovery**

- Vercel, State of agent skills: https://vercel.com/blog/state-of-agent-skills
- Agents Index, State of MCP servers (2026-11-23): https://agentsindex.org/reports/state-of-mcp/
- Stork, MCP Registry endpoints checked (2026-11-16): https://www.stork.ai/blog/mcp-registry-endpoints-checked-november-2026
- Smithery, The future of Smithery: https://www.smithery.ai/future

**Native selection**

- Anthropic tool search: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool

**Security**

- Snyk, Re-assessing the security of the agent skill ecosystem: https://research.snyk.io/blog/skill-analysis/
- Trail of Bits, The sorry state of skill distribution (2026-06-03): https://blog.trailofbits.com/2026/06/03/the-sorry-state-of-skill-distribution/
- Zenity / BusinessWire (2026-08-06): https://www.businesswire.com/news/home/20260806707467/en/

**Competitors**

- Skills Manager: https://www.skillsmanager.dev/ ; https://github.com/xingkongliang/skills-manager
- Skillporter: https://www.skillporter.dev/
- Capa: https://github.com/infragate/capa
- LazySkills: https://lazyskills.sh/ ; https://github.com/alvinunreal/lazyskills
- Dalo: https://dalo.sh/ ; https://github.com/sebastian-software/dalo
- SkillDoctor: https://github.com/Dongdarealest/skilldoctor
- Credenzo: https://credenzo.io/ ; https://github.com/adudley78/credenzo
- Ctx: https://github.com/stevesolun/ctx
- CC Switch: https://github.com/farion1231/cc-switch
- MetaMCP: https://github.com/metatool-ai/metamcp

**Unverified / leads only**

- Specific dates and star counts of some new 2026 projects will keep changing; star counts in the table are Shields.io readings on 2026-11-23.
- Individual quotes from gstack #1108 and Reddit/HN were not opened item by item this time and are not included in the conclusions.
- Capa's 35–39 agent count comes from its README/existing assessments and was not checked version by version this time.
