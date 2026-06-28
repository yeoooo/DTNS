# Trend Agent

The Trend Agent reads one topic article file and writes one topic trend file.
It works for any topic passed with `--topic`.

Large inputs are processed as resumable Map-Reduce work: up to 12 projected
articles per Map request and up to 16 candidates per Reduce request. Internal
checkpoints are stored below
`.state/trend/<topic>/<run_id>/`; only the final trend file is public to later
pipeline stages.

Default input:

- `topic_articles.json`

Default output:

- `topic_trends.json`

Usage:

```bash
python -m dtns.agents.trend \
  --topic technology \
  --input topic_articles.json \
  --output topic_trends.json
```

Use `--run-id` to resume a selected run and `--state-path` to override its
checkpoint directory. Checkpoints are reused only while the input and policy
fingerprints match.

Known topic prompts are loaded from `src/dtns/prompts/trend_<topic>.md` when
available. Other topics use the generic trend prompt.

The Trend Agent emits JSON only. It does not write Markdown, publish, or write
newsletters.
