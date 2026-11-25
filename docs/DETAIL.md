# CapRouter capability bus

Adds the CapRouter MCP server to DeepSeek Harness. CapRouter is a local ledger for the skills,
plugins, MCP servers and CLI agents already installed on your machine: it indexes them, routes a need
to a candidate, runs deterministic probes and acceptance checks, and records outcomes.

## What it adds

The `core` tool profile exposes ten `mcp__caprouter__*` tools: `capability_search`,
`capability_get`, `capability_invoke`, `capability_probe`, `capability_probe_record`,
`capability_verify`, `capability_record_outcome`, `capability_list_gaps`, `capability_install` and
`capability_uninstall`. Switch the patch to `--tools full` for all twenty, including discovery, card
drafts, versions, health, duplicates, lock, drift and deps.

## Requirements

- Python 3.11 or newer.
- CapRouter installed for that interpreter: `pip install caprouter` (or `pip install .` in a
  checkout). The bundle starts `python -m caprouter mcp`, so nothing is vendored into this package.

## How it runs

- interpreter: `CAPROUTER_PYTHON`, else `py` on Windows and `python3` elsewhere
- command: `<python> -m caprouter mcp --tools core --host deepseek-harness`
- transport: stdio

The workspace is resolved at startup: `CAPROUTER_WORKSPACE`, then a workspace found above the
session directory, then `~/CapRouter/workspace` (`%USERPROFILE%\CapRouter\workspace` on Windows).
Create it once with:

```bash
caprouter init --workspace ~/CapRouter/workspace
```

## Limits

- CapRouter never installs or executes anything without an explicit approval: `install` copies files,
  and command execution needs a reviewed Card permission plus `allow_execute`.
- Search abstains when it is not confident; an empty result is a supported answer.
- Probe evidence marked `static` proves that a capability is present and invokable, not that it
  produces good output. Behavioral evidence must be recorded by the host.
- The workspace is local state: its registry, probes and outcomes stay on your machine and are never
  sent anywhere by this bundle.
