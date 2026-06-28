# Trend Batch Processing Contract

This contract defines the Trend Agent's internal Map-Reduce and recovery
boundary. The public stage contract remains unchanged:

```text
<topic>_articles.json -> <topic>_trends.json
```

Intermediate candidate files are private to the Trend Agent. Editor and
Publisher stages must read only the final topic trends file.

## Goals

- Prevent unbounded prompts and truncated JSON responses.
- Preserve cross-article trend discovery through a bounded reduction phase.
- Resume failed runs without repeating completed model work.
- Produce at most eight concise final trends for one newsletter topic.

## Storage Layout

```text
data/.state/trend/<topic>/<run_id>/
  map-000000-000012.json
  map-000012-000024.json
  reduce-000-000000-000016.json
  reduce-final.json
```

Every file must validate against `trend_candidate_checkpoint.schema.json` and
must be written atomically through a temporary file followed by a rename.

## Identity And Reuse

- `input_fingerprint` is the lowercase SHA-256 digest of the exact topic
  article input bytes.
- `policy_fingerprint` covers the topic prompt, candidate schema, public trends
  schema, model configuration, limits, and generation configuration.
- A checkpoint is reusable only when its topic and both fingerprints match.
- Changed input, prompts, schemas, models, or limits require a new run.

## Input Projection

The Trend model must not receive the complete classifier document. Each input
article is projected to:

- `id`
- `title`
- `published_at`
- summary truncated to 500 characters
- at most 6 tags
- at most 6 technologies
- at most 4 domains

Canonical URLs, source payloads, AI rationale, and classification rule details
must not be sent to the model. The Editor receives URLs later from the topic
article contract.

## Map Phase

- Default batch size: 12 articles.
- Process batches in input order.
- Generate at most 4 candidate trends per map batch.
- Use Gemini Structured Output with the candidate portion of
  `trend_candidate_checkpoint.schema.json`.
- The model returns candidate fields only. Runtime code supplies topic,
  timestamps, fingerprints, checkpoint IDs, and actual model name.
- Every candidate must reference only article IDs from its map batch.

## Reduce Phase

- Merge candidate trends instead of sending all original articles again.
- A reduce request accepts at most 16 candidate trends.
- Merge duplicate and closely related candidates while preserving the union of
  their valid article IDs.
- Produce at most 8 candidates per reduce request.
- If more than 16 candidates remain, reduce them in deterministic groups and
  repeat at the next level until one final group remains.
- The final output contains at most 8 trends.
- Runtime code supplies `schema_version`, `generated_at`, `topic`, and `period`;
  the model must not generate those deterministic fields.

## Output Limits

Each candidate or final trend is limited to:

- title: 120 characters
- summary: 500 characters
- why-it-matters: 500 characters
- article IDs: 20
- keywords: 8 items, 80 characters each

The final output must validate against `trends.schema.json`. All referenced
article IDs must exist in the original topic article input.

## Model Policy

- Primary model: `gemini-3.5-flash`.
- Fallback model: `gemini-3.1-flash-lite`.
- HTTP 429 and 5xx handling follows the shared Gemini retry policy.
- Checkpoints record the model that produced the accepted response.
- After fallback succeeds, later Map and Reduce requests in the same run should
  use fallback first.

## Response Validation

A model response is accepted only when:

- The finish reason does not indicate output truncation.
- The response is valid JSON.
- The response satisfies the Structured Output schema.
- Candidate IDs are unique within the checkpoint.
- Article IDs are unique, known, and allowed for the current phase.
- All count and string-length limits are satisfied.

Invalid responses must not be written as checkpoints.

## Recovery Policy

The following failures are recoverable content failures:

- output token limit or truncated response
- empty or invalid JSON
- schema validation failure
- duplicate, missing, or unknown IDs
- configured output limit violation

Map recovery:

1. Retry the same map batch once.
2. If it still fails and contains multiple articles, split at the stable
   midpoint.
3. Process the left child before the right child.
4. A failed single-article map batch is terminal.

Reduce recovery:

1. Retry the same reduce group once.
2. If it still fails and contains multiple candidates, split the group at the
   stable midpoint.
3. Reduce each child and then reduce the child outputs together.
4. A failed single-candidate reduce group is terminal.

Increasing output tokens alone is not a recovery strategy. Batch reduction and
schema limits must remain active even when model token limits increase.

## Resume Rules

- Load checkpoints only from the selected topic and run directory.
- Reject invalid schemas, mismatched fingerprints, duplicate checkpoint IDs,
  overlapping map ranges, and unknown article IDs.
- Skip only completed valid checkpoints.
- Resume from the first uncovered map range or unfinished reduce level.
- Never reuse candidates from another topic.

## Finalization

- Preserve deterministic trend order: importance, then earliest source article
  position, then candidate ID.
- Require unique final trend IDs.
- Validate every final article reference against the original input.
- Write the topic trends file atomically only after complete validation.
- Do not replace an existing valid final output when the new run fails.

## Failure Contract

Terminal errors must include:

- run ID, topic, phase, and checkpoint ID
- source article or candidate IDs
- primary and fallback models attempted
- attempt count
- failure category: `transient_api`, `max_tokens`, `invalid_json`,
  `invalid_schema`, `id_mismatch`, `checkpoint_io`, or `internal_error`

Errors must not contain API keys, webhook URLs, full prompts, full article
bodies, or raw model responses.
