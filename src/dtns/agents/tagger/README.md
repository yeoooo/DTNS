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
