# CapRouter Mod (Claude Code)

Host takeover layer for Claude Code:

- On `prompt.submit`, it reads `~/.caprouter/state.json`; when enabled and the prompt
  looks like a multi-step task, it adds a CapRouter context line **only Claude reads**.
- Registers `/caprouter` to print the current ON/OFF status.
- If the plugin's `UserPromptSubmit` hook already added the same context, the mod
  detects it and does not duplicate.

## Requirements

- Claude Code **v2.1.287+** (mods support)
- Claude Code CLI, or `npx` (the validation below works without login)

## Validate and load

```bash
cd mods/caprouter-mod
# If `claude` is not installed on PATH, use npx:
npx -y @anthropic-ai/claude-code@latest plugin validate .
# Then, in a logged-in session:
claude --plugin-dir .
```

Then in the session:

```
/caprouter
```

Expected: `CapRouter: enabled` or `CapRouter: disabled`.

## Mock tests (no Claude Code needed)

```bash
node tests/register.test.mjs
node --check hooks/register.js
```

The mock test covers:
- `session.start` registers `/caprouter`;
- `prompt.submit` adds context when enabled;
- no context when disabled or for trivial prompts;
- no duplicate when the hook already added CapRouter context.

## Files

```
.claude-plugin/plugin.json    manifest
hooks/hooks.json              {"modules": ["./register.js"]}
hooks/register.js             prompt.submit + command.run handlers
tests/register.test.mjs       Node mock test
package.json                  {"type": "module"}
```

## Status

Delivered, mock-tested, and **statically validated** with Claude Code 2.1.329 via:

```bash
npx -y @anthropic-ai/claude-code@latest plugin validate mods/caprouter-mod
```

Result: `✔ Validation passed` (no errors, no warnings after adding the icon).

The interactive runtime path (`claude --plugin-dir .`) still needs a logged-in session;
that is the only unverified layer.
