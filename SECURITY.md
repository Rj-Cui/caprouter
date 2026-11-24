# Security Policy

## Threat model

CapRouter indexes and can invoke local capabilities (skills, plugins, projects, CLI agents,
MCP server definitions). Installing a capability is equivalent to running third-party code
with your user privileges.

Defenses in place:

- online content only enters `.caprouter/quarantine/`; `fetch` neither installs nor executes it;
- `fetch --url` only accepts https ZIPs from an allowlist and re-checks redirects;
- install requires an explicit `--approve`; previews are hash-pinned and a changed source is refused;
- `provenance.content_hash` is verified before install; a mismatch blocks unless explicitly overridden;
- local installs refuse symlinks by default; ZIP install rejects traversal, symlinks, encrypted
  members and oversize files;
- installs write license/dependency metadata and a transaction journal;
- `adopt` copies an external skill; the original path is never moved or modified;
- command execution requires four conditions plus the global `state` switch;
- MCP `capability_install` requires `approve_hash` from a preview, not a bare approval flag;
- execution and refusal decisions are written to `~/.caprouter/audit.jsonl`;
- `uninstall` / `rollback` / `trash --clean` preview by default and require `--yes`.

Signature / trust root:

- **not implemented**. The hash chain (preview hash, `provenance.content_hash`, manifest hash)
  proves integrity, not publisher identity. Install-time signatures and trust roots are
  deliberately deferred.
- `deps plan` / `deps check` are read-only. They never execute pip/npm/pnpm/yarn/uv/poetry;
  any install command they print must be run explicitly by the user or host.

Not defended:

- no OS-level sandbox; `permissions.net` is declarative and cannot stop a child process from
  using the network;
- absolute-path writes outside the working directory are not blocked;
- no publisher signature/trust root; license detection is a text heuristic, not legal advice;
- the MCP server is stdio-only and has no authentication by design.

## Reporting a vulnerability

Open a private security advisory on the repository. Do not paste malicious skill content into a
public issue. Include reproduction steps, the CapRouter version (`caprouter --version`), and the
affected component (scan / review / install / quarantine / invoke / MCP).
