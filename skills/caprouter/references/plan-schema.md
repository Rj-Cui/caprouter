# CapRouter Plan Contract

A plan is a JSON object under `.caprouter/plans/`.

```json
{
  "task_id": "market-scan",
  "goal": "Research a target market and deliver a verifiable report",
  "quality_gate": {"max_attempts": 2, "on_limit": "pause_and_report"},
  "nodes": [
    {
      "id": "n1",
      "title": "Search public sources",
      "capability": "retrieve",
      "domain": "research",
      "brief": "Search public sources on 5 platforms; return only URL, title and platform",
      "output_contract": {"type": "url_list", "format": "json"},
      "acceptance": ["At least 20 URLs", "Each entry includes platform and title"]
    }
  ]
}
```

## Rules

- `task_id`, `goal`, `nodes` are required.
- Each node needs `id`, a non-empty `brief`, a single `output_contract`, and at least one `acceptance` item.
- A node that says "write a 1500-word report" cannot have a `url_list` contract; `plan check` rejects it.
- `max_turns` above 12 is a warning: prefer a deterministic tool or split the node.
- `capability` and `domain` are optional but strongly recommended; without them routing is lexical only.

## Verification

Each node's acceptance should be testable. The verifier should receive claims, source metadata and snapshot excerpts, not the writer's reasoning.
