# Editor Agents

Editor agents read topic trends, optionally enrich them with topic article
metadata, and write Korean Markdown newsletters for any topic.

Inputs:

- `topic_trends.json`
- optional `topic_articles.json`

Outputs:

- `<topic>_newsletter.md` (or the explicit output path)

Internal state:

- `.state/editor/<topic>/<run_id>/candidate.md`
- `.state/editor/<topic>/<run_id>/checkpoint.json`

The final file is replaced atomically only after the candidate passes the
newsletter contract. A matching checkpoint is resumed without another model
request; malformed, truncated, overlong, or unknown-URL output is rejected.

Prompt files:

- `src/dtns/prompts/editor_<topic>.md` when present

Editors should write natural Korean Markdown, summarize trends and supplied
articles, generate weekly insights, explain why trends matter, keep technical
names in English, avoid full article translation, and never fabricate missing
information. When article metadata is supplied, editors should cite original
article URLs.
