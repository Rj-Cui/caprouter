---
name: caprouter
description: Discover, probe, invoke and verify local capabilities via the CapRouter MCP bus before improvising or declaring done.
---

# CapRouter

Search first, execute second, verify last.

## Start

Call `capability_search`. If the registry is missing, run
`python "${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py" init` and retry; when disabled,
use the normal flow.

## Flow

1. **Plan**: split subtasks; state inputs, outputs and acceptance.
2. **Select**: `capability_search(task, capabilities?, domains?)`; use
   `selected` plus `capability_get`. On `abstain`, never force a choice: read
   `capability_list_gaps`, then follow `capability-author`
   (`synthesize propose → refine → human approve`).
3. **Probe**: `capability_probe(id)` runs deterministic checks; after a pending
   host task runs, backfill with
   `capability_probe_record(id, task, status, duration_ms?)`; static is not
   behavioral proof.
4. **Remote gap**: only after local `abstain` and explicit consent to go
   online, call `capability_discover(query, allow_network=true)` for skills.sh
   candidates only. Download/install must use the CLI:
   `fetch → vendor → install --approve → review`.
5. **Execute**: prompt mode uses the skill path from `capability_invoke`;
   command mode passes `allow_execute=true` only with explicit approval; never
   guess a capability from its name.
6. **Verify**: `capability_verify(artifact, acceptance)`; only `pass` means
   done; `fail`/`unverifiable` returns issues for rework, max 2 rounds.
7. **Record**: `capability_record_outcome` stores success, duration and
   human_edit_minutes.

## Install and Card

New capabilities: CLI `install --source <dir|zip> --approve` (no script run).
Draft with `capability_card_draft` or `capability draft`; `--apply` after human
review.

## Bans

Do not skip search and hand-scan skill dirs; do not skip verify; do not run
local commands unapproved; do not replace deterministic checks with an LLM
review.

Contract: `references/capability-bus.md`.
