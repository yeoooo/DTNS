# Trend Agent

The Trend Agent reads one topic article file and writes one topic trend file.
It works for any topic passed with `--topic`.

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

Known topic prompts are loaded from `src/dtns/prompts/trend_<topic>.md` when
available. Other topics use the generic trend prompt.

The Trend Agent emits JSON only. It does not write Markdown, publish, or write
newsletters.
