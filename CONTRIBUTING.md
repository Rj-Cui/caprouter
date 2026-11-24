# Contributing

## Development setup

Python 3.11+, no runtime third-party dependencies.

```bash
git clone https://github.com/Rj-Cui/caprouter.git
cd caprouter
python -m unittest discover -s tests
python bin/caprouter.py doctor --workspace ./workspace
```

Optional editable install:

```bash
pip install -e .
caprouter --version
```

## Rules

- Every behavior change needs a unit test; keep the core dependency-free.
- Do not weaken review thresholds to raise auto-apply rates.
- Do not add automatic install or execution paths for remote content.
- Keep `skills/caprouter/SKILL.md` at or below 2048 bytes (`doctor` enforces this).
- Do not commit `workspace/.caprouter/` runtime data: it contains machine-specific paths and
  personal outcomes.
- Prefer explicit `abstain`, `unverifiable` and warnings over guessing.

## Non-goals

- A skill / MCP marketplace;
- a model / provider gateway;
- a general multi-agent orchestrator;
- competing with native tool search on recall.

## Pull requests

Describe the user-visible change, how you verified it, and paste the test command output.
For security-sensitive changes (install, quarantine, invoke, guard), explain the failure path
you tested.
