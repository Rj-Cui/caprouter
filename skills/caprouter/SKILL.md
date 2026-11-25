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

## Core tools (ten)

`capability_search`, `capability_get`, `capability_invoke`, `capability_probe`,
`capability_probe_record`, `capability_verify`, `capability_record_outcome`,
`capability_list_gaps`, `capability_install`, `capability_uninstall`.
Discovery, card drafts, versions, health, duplicates, lock, drift, deps, probe
reports and gaps are CLI-only; never call a `capability_*` tool outside that list.

## Flow

1. **Plan**: subtasks with inputs, outputs, acceptance.
2. **Select**: `capability_search(task, capabilities?, domains?)` then
   `capability_get`. On `abstain` never force a choice: read
   `capability_list_gaps`, then follow `capability-author`.
3. **Probe**: `capability_probe(id)`; backfill a host run with
   `capability_probe_record(id, task, status)`. Static is not behavioral proof.
4. **Remote gap**: only with consent, and it is CLI-only:
   `caprouter discover --allow-network`, then `fetch`, `vendor`, `install`.
5. **Execute**: prompt mode uses the path from `capability_invoke`; command mode
   needs `allow_execute=true` and explicit approval; never guess by name.
6. **Verify**: `capability_verify(artifact, acceptance)`; only `pass` is done;
   `fail`/`unverifiable` means rework, max 2 rounds.
7. **Record**: `capability_record_outcome` with success and edits.

## Install and Card

`install --source <dir|zip> --approve` copies files, never runs them. Cards:
`capability draft --id <id>`, then `review`, then `--apply` after human review.

## Bans

Do not hand-scan instead of searching, skip verify, run unapproved commands, or
replace deterministic checks with an LLM review.

Contract: `references/capability-bus.md`.
