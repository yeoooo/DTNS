# Tagger Batch Processing Contract

This contract defines the Tagger's internal checkpoint and recovery boundary.
It preserves the public stage contract:

```text
normalized_articles.json -> tagged_articles.json
```

Batch checkpoints are implementation artifacts. No downstream stage may read
them directly.

## Goals

- Bound Gemini input and output size.
- Recover from truncated or invalid JSON without repeating successful work.
- Resume a failed Tagger run deterministically.
- Preserve article order and exactly-once inclusion in the final output.

## Storage Layout

```text
data/.state/tagger/<run_id>/
  articles-000000-000008.json
  articles-000008-000012.json
  articles-000012-000016.json
```

Each file must validate against `tagger_batch_checkpoint.schema.json`.
Checkpoint files contain completed batches only and must be written atomically
through a temporary file followed by a rename.

## Identity

- `run_id` identifies one resumable Tagger run.
- `input_fingerprint` is the lowercase SHA-256 digest of the exact
  `normalized_articles.json` bytes.
- `policy_fingerprint` is the lowercase SHA-256 digest of the Tagger prompt,
  checkpoint schema version, model configuration, batch limits, and output
  limits.
- A checkpoint is reusable only when both fingerprints match the current run.
- `batch_id` is derived from the zero-based half-open input range using
  `articles-<start>-<end>` with six-digit indexes.

## Initial Batching

- The default initial batch size is 8 articles.
- Input order from `normalized_articles.json` must be preserved.
- The model output must contain only article ID, tags, technologies, domains,
  confidence, and optional rationale.
- Output limits are 6 tags, 6 technologies, 4 domains, and 160 rationale
  characters per article.
- Gemini Structured Output must use the checkpoint article schema.

## Model Policy

- Primary model: `gemini-3.5-flash`.
- Fallback model: `gemini-3.1-flash-lite`.
- HTTP 429 and 5xx handling follows the shared Gemini retry policy.
- The checkpoint and final `ai_metadata.model` must record the model that
  produced the accepted response, not the requested primary model.
- Once the primary model is exhausted and fallback succeeds, subsequent
  batches in the same run should use fallback first to avoid repeated probes.

## Response Validation

A batch succeeds only when all of the following are true:

- The response finish reason does not indicate output truncation.
- The response is valid JSON and satisfies the structured output schema.
- Every requested article ID appears exactly once.
- No unrequested article ID appears.
- All configured field limits are satisfied.

An invalid response must never be written as a checkpoint.

## Adaptive Split Policy

The following failures trigger adaptive splitting:

- Output token limit or truncated response.
- Empty or invalid JSON.
- Schema validation failure.
- Missing, duplicate, or unexpected article IDs.

For a failed batch:

1. Retry the same batch once.
2. If it still fails and contains more than one article, split it at the stable
   midpoint.
3. Process the left child before the right child.
4. Repeat until each child succeeds or a single-article batch fails.
5. A failed single-article batch is terminal and must report its article ID and
   the original error chain.

Child batch ranges and IDs must remain half-open and non-overlapping. A parent
checkpoint must not coexist with child checkpoints covering the same range.

## Resume Rules

- Scan only the state directory for the selected `run_id`.
- Reject checkpoints with mismatched fingerprints or invalid schemas.
- Reject overlapping ranges, duplicate article IDs, and IDs absent from the
  current input.
- Skip only valid completed ranges.
- Continue from the first uncovered input range.
- A new policy or changed input starts a new run and must not reuse stale
  checkpoints.

## Finalization

- Merge checkpoint articles in original input order.
- Require exactly one tagged result for every normalized input article.
- Validate the merged document against `tagged_articles.schema.json`.
- Write `tagged_articles.json` atomically only after full validation.
- Do not modify or partially overwrite an existing valid final output when a
  run fails.
- Checkpoints may be retained for audit or removed only after final output is
  durably written.

## Failure Contract

Terminal errors must include:

- `run_id`.
- `batch_id`.
- Failing article IDs.
- Primary and fallback models attempted.
- Attempt count.
- Failure category such as `transient_api`, `max_tokens`, `invalid_json`,
  `invalid_schema`, or `article_id_mismatch`.

Errors must not include API keys, webhook URLs, full prompts, or full article
bodies.
