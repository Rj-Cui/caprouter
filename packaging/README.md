# Packaging

These files are templates; none of them publishes anything by themselves.

| File | Purpose |
|---|---|
| `../.github/workflows/publish.yml` | Build sdist/wheel and publish to PyPI via Trusted Publishing |
| `homebrew/caprouter.rb` | Homebrew formula template (fill `url`/`sha256` after the first PyPI release) |
| `build-binary.md` | PyInstaller one-file build notes |
| `claude-plugin-marketplace.md` | How to submit the Claude Code plugin |

## PyPI checklist

1. Confirm the project name `caprouter` is still available (it was on 2026-11-23).
2. Configure a PyPI Trusted Publisher for the GitHub repo/environment `pypi`.
3. Create a GitHub release; the `publish` workflow builds and uploads.
4. Verify from a clean machine:

```bash
uvx --from caprouter caprouter --version
pipx run --spec caprouter caprouter --version
```

The wheel ships the CLI and MCP server only. Hooks, slash commands and built-in skills are
part of the Claude Code plugin (the repository root), not the wheel.
