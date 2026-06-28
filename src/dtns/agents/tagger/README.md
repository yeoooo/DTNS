# Tagger Agent

The tagger reads `normalized_articles.json` and writes `tagged_articles.json`.

It is responsible for AI-assisted enrichment:

- technologies
- technical tags
- domains
- confidence
- short rationale

Prompt file:

- `src/dtns/prompts/tagger.md`

The output must be JSON that conforms to
`docs/contracts/tagged_articles.schema.json`.

Completed batches are stored under `data/.state/tagger/<run_id>/`. The default
run ID is derived from the exact input bytes and the active Tagger policy, so a
rerun resumes only checkpoints produced from the same input and policy.

The initial batch size is eight articles. Invalid or truncated responses are
retried once and then split at a stable midpoint until they succeed or a
single-article batch fails.
