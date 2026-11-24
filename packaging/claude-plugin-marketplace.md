# Claude Code plugin listing

The repository root is already a plugin root:

```text
.claude-plugin/plugin.json    manifest
.mcp.json                     starts the CapRouter MCP server
skills/                       caprouter + capability-author
commands/                     /caprouter:* entries
hooks/                        UserPromptSubmit reminder + PreToolUse guard
```

To list it in a marketplace:

1. Validate the plugin:

```bash
npx -y @anthropic-ai/claude-code@latest plugin validate .
```

2. Publish the repository (GitHub) and add a marketplace entry in the marketplace repository
   using the current Claude Code marketplace schema. Do not invent a schema here; use the
   format accepted by the marketplace you are submitting to.
3. Keep `plugin.json` `version` aligned with `caprouter/__init__.py` and `pyproject.toml`.
4. The official directory is curated; expect review. A community marketplace can be used first.

The plugin and the PyPI package are separate distribution channels:

- PyPI/uvx: CLI + MCP server.
- Plugin: hooks, slash commands, built-in skills, plus the MCP server.

Keep both in sync on every release.
