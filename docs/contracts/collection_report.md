# Collection Report Contract

This contract records source-level Collector health without changing
`articles.json`.

## Storage

```text
data/.state/collector/<run_id>/collection_report.json
```

The report validates against `collection_report.schema.json` and is written
atomically after all configured sources have been attempted.

## Rules

- Record one entry for every configured source in configuration order.
- Distinguish a successful empty feed from a failed request.
- Record fetched and accepted counts after source-level deduplication.
- Mark the run completed when all sources succeed, partial when some succeed,
  and failed when no source succeeds.
- Keep the existing behavior that one failed source does not stop other
  sources.
- Pipeline policy may define a minimum success ratio before AI stages run.
- Error categories must be sanitized and must not contain response bodies,
  credentials, cookies, or request headers.
- Downstream stages continue to consume only `articles.json`.

## Semantic Validation

Consumers must validate the following cross-field rules in addition to JSON
Schema validation because JSON Schema cannot compare sibling field values:

- `accepted_count` must not exceed `fetched_count`.
- `finished_at` must not precede `started_at`.
