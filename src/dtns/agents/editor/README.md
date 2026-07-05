# Editor Agents

Editor agents read topic trends and topic article metadata, ask Gemini for a
URL-free structured draft, and deterministically render Korean Markdown.

Inputs:

- `topic_trends.json`
- `topic_articles.json` for every non-empty trend input

Outputs:

- `<topic>_newsletter.md` (or the explicit output path)

Internal state:

- `.state/editor/<topic>/<run_id>/candidate.md`
- `.state/editor/<topic>/<run_id>/editor_draft.json`
- `.state/editor/<topic>/<run_id>/checkpoint.json`

The final file is replaced atomically only after the candidate passes the
newsletter contract. A matching checkpoint is resumed without another model
request. Gemini never receives article URLs and returns prose plus IDs as JSON;
the renderer alone resolves IDs to stored titles and canonical URLs.

Prompt files:

- `src/dtns/prompts/editor_<topic>.md` when present

Editors should write natural Korean prose, summarize trends and supplied
articles, generate weekly insights, explain why trends matter, keep technical
names in English, avoid full article translation, and never fabricate missing
information.
