# Pull request

## What this changes

<!-- One or two sentences. Link the issue it fixes, if any. -->

## Checklist

- [ ] `python -m unittest discover -s tests` passes locally (the suite is stdlib-only).
- [ ] No machine-specific data: no absolute local paths, usernames, tokens or workspace contents.
- [ ] Documentation updated when behavior or limits changed (`README.md`, `docs/`, `CHANGELOG.md`).
- [ ] Security-relevant changes state the boundary they keep: install copies files, execution needs
      a reviewed Card permission plus `allow_execute`, remote content stays quarantined.
- [ ] `skills/caprouter/SKILL.md` stays at or below 2048 bytes and only calls tools that exist in the
      `core` MCP profile.
