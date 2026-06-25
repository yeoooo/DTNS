# Preprocessors

Preprocessors read `articles.json` and write `normalized_articles.json`.

Responsibilities:

- Normalize titles, URLs, timestamps, source names, and language.
- Clean tracking parameters from URLs.
- Deduplicate articles.
- Generate stable IDs.
- Validate required fields.

No AI is allowed in this module.
