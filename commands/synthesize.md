---
description: Turn a recorded capability gap into a candidate skill/script, refine it with builtin rules or a local CLI adapter, and approve only after human review.
argument-hint: propose|list|refine|approve|improve [args]
---

Run CapRouter's post-task synthesis pipeline:

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py" synthesize $ARGUMENTS --workspace "${CAPROUTER_WORKSPACE:-${CLAUDE_PLUGIN_ROOT:-.}/workspace}"
```

Rules:

1. `propose` only after `capability search` abstained or clearly mis-selected, and a gap was recorded.
   Never use synthesis to avoid searching for an existing working tool.
2. `propose` writes a candidate under `.caprouter/synthesized/`; it does **not** register or execute it.
3. `refine` only rewrites the candidate and its Card description/examples. It does not approve,
   install, or verify. Use `--method auto` unless the user names a method.
4. `approve` is a human decision: show the candidate path, refined `SKILL.md`, `card_draft.json`,
   permissions and warnings first. `--apply` is a separate explicit choice.
5. `--auto` reuses the built-in Card review: install, review the real Card, keep it only when
   `auto_approved`; otherwise roll back the install/draft/registry and leave the candidate
   `proposed`. It does not run semantic review and does not apply the Card unless `--apply`.
6. Generated scripts are inert skeletons: no network, no subprocess, read-only by default.
   Never run one without reviewing it and obtaining authorization.
7. After approval, run `capability probe` and record a real `capability outcome`; only then
   is the capability validated. `improve` generates a patch, not an automatic replacement.

Details: `skills/capability-author/SKILL.md` and `docs/capability-bus.md` §16.
