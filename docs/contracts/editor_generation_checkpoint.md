# Editor Generation Checkpoint Contract

This contract prevents partial or truncated newsletter Markdown from replacing
a valid final newsletter.

## Storage

```text
data/.state/editor/<topic>/<run_id>/
  candidate.md
  checkpoint.json
```

`checkpoint.json` validates against
`editor_generation_checkpoint.schema.json`. Both files are internal to Editor.

## Generation And Validation

- Editor accepts at most eight final trends from the Trend contract.
- Detect output-token or length finish reasons before accepting Markdown.
- Normalize Discord-incompatible separators and level-four headings.
- Require title, summary, trends, and insights sections.
- Require all supplied article links to use known input URLs.
- Reject JSON, front matter, code fences, empty output, and output over 12,000
  characters.
- Retry a recoverable content failure once, then use the configured fallback
  model according to the shared AI execution state.

## Checkpoint And Finalization

- Write `candidate.md` atomically only after validation.
- Write its checkpoint only after the candidate is durable.
- Resume only when input, policy, candidate hash, and topic all match.
- Copy the validated candidate to `<topic>_newsletter.md` atomically.
- Never replace an existing valid newsletter after a failed generation.
- Store only fingerprints and validation results, never prompts or raw model
  responses.
