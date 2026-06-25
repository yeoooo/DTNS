# Classifier

The classifier reads `tagged_articles.json` and writes:

- `technology_articles.json`
- `backend_articles.json`
- `qa_articles.json`

Classification is deterministic and multi-label.

Use transparent rules based on:

- tags
- technologies
- domains
- source metadata
- known project maps

Do not use AI for initial classification.
