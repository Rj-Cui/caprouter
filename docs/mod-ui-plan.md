# CapRouter Mod UI Plan (mock implemented, real-machine verification pending)

## Why write this separately

One of the "Top 5 worth borrowing" items is: **use hooks for enforcement, use mods for UI**. hooks are already implemented; the mod code has been delivered in `mods/caprouter-mod/` and has passed `node --check`, Node mock event tests, and **`plugin validate` of Claude Code 2.1.329** (`✔ Validation passed`, no warnings). What remains undone is interactive runtime verification after login.

## What a mod is

A mod is a Claude Code plugin with a hooks module: a JavaScript/TypeScript file whose functions Claude Code calls on events. It can add panes, commands and tool-call rules, and draw in the interface.
(Source: https://code.claude.com/docs/en/plugins/mods/overview.md)

## Directory structure (official example)

```
caprouter-mod/
├── .claude-plugin/
│   └── plugin.json
└── hooks/
    ├── hooks.json          # {"modules": ["./register.js"]}
    └── register.js         # export function register(on) { ... }
```

The `modules` key in `hooks.json` is the mark of "this is a mod":

```json
{
  "description": "CapRouter UI module",
  "modules": ["./register.js"]
}
```

## Minimal event plan

```js
export function register(on) {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'caprouter',
      description: 'Show CapRouter status or toggle it'
    })
    return next(e)
  })

  on('command.run', { command: 'caprouter' }, async () => {
    // Read the state and return text; how exactly to get the state depends on the types of the current version
    return { text: 'CapRouter: use `caprouter status` for state' }
  })

  on('ui.render', { component: 'Spinner' }, async ($, e, next) => {
    // Can show ON/OFF after the spinner
    return next({ ...e, props: { ...e.props, suffix: ' · CapRouter' } })
  })
}
```

- Input bar buttons/panels: mods can draw panes, buttons and fields; for the specific element API see `mods/reference.md#elements`.
- Command registration: `session.start` + `$.command.register`.
- Reading and writing state: prefer the mods API; if the API does not provide file read/write, only do "display + guide the user to toggle via the tray/CLI", and do not hard-write the state file.

## Verification steps

1. `claude --version` confirms ≥2.1.287 (verified with npx 2.1.329).
2. `npx -y @anthropic-ai/claude-code@latest plugin validate ./caprouter-mod` (passed).
3. `claude --plugin-dir ./caprouter-mod` starts a login session (not run).
4. Type `/caprouter`, check whether the command appears and the status is correct.
5. Use `claude plugin test` to write a minimal event test.

## Decision

- **Claude Code**: `mods/caprouter-mod` has implemented `prompt.submit` takeover and the `/caprouter` status command;
  once the CLI exists, first run `claude plugin validate .`, then decide whether to use it to replace the `UserPromptSubmit` hook in the plugin.
- **Cherry / other hosts**: keep the cross-platform Python tray + `caprouter state` command.
- Do not claim the mod has passed real-machine verification without the CLI; mock tests only prove logic and syntax.
