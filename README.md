# DTNS

DTNS is an AI-powered weekly engineering newsletter CLI.

It collects engineering news, tags articles with AI, classifies articles into
multiple newsletter topics, discovers weekly trends, writes Korean newsletters,
and publishes them to Discord Webhooks.

The project is designed for open-source maintainability. Architecture,
contracts, prompts, and module boundaries are intentionally defined before full
feature implementation.

## Topics

DTNS supports three independent newsletters.

| Topic | Purpose |
| --- | --- |
| `technology` | Ecosystem-wide software engineering trends |
| `backend` | Backend engineering, infrastructure, APIs, data systems, and production systems |
| `qa` | QA, test automation, quality gates, and quality engineering |

Articles are multi-label. For example, an OpenTelemetry release can appear in
both `technology` and `backend`; a Testcontainers update can appear in both
`backend` and `qa`.

## Pipeline

```text
Collector
  -> articles.json
Preprocessor
  -> normalized_articles.json
Tagger Agent
  -> tagged_articles.json
Classifier
  -> technology_articles.json
  -> backend_articles.json
  -> qa_articles.json
Trend Agent per topic
  -> technology_trends.json
  -> backend_trends.json
  -> qa_trends.json
Editor Agent per topic
  -> technology_newsletter.md
  -> backend_newsletter.md
  -> qa_newsletter.md
Publisher
  -> Discord Webhook per topic
```

Every stage communicates through files. No stage should depend on another
stage's implementation details.

## Responsibilities

- Collector: collect raw article metadata only. No AI.
- Preprocessor: normalize, deduplicate, clean URLs, assign stable IDs, validate.
  No AI.
- Tagger Agent: identify technologies, domains, and technical tags using AI.
- Classifier: deterministic multi-label topic classification.
- Trend Agent: topic-specific AI clustering and trend discovery. JSON only.
- Editor Agent: topic-specific Korean Markdown newsletter writing.
- Publisher: Discord Webhook delivery. No AI.

## Repository Map

```text
AGENTS.md
docs/
  architecture.md
  decisions.md
  contracts/
src/dtns/
  cli.py
  collectors/
  preprocessors/
  classifier/
  agents/
    tagger/
    trend/
    editor/
  publisher/
  contracts/
  prompts/
```

New work should follow `AGENTS.md`, `docs/architecture.md`, and
`docs/decisions.md`.

## Contracts

Contracts live in `docs/contracts/`.

- `articles.schema.json`
- `normalized_articles.schema.json`
- `tagged_articles.schema.json`
- `topic_articles.schema.json`
- `trends.schema.json`
- `newsletter.md`

## Prompts

Prompt templates live in `src/dtns/prompts/`.

- `tagger.md`
- `trend_technology.md`
- `trend_backend.md`
- `trend_qa.md`
- `editor_technology.md`
- `editor_backend.md`
- `editor_qa.md`

Agent implementations should load these files instead of embedding large prompt
strings in Python code.

## CLI Contract

```bash
newsletter collect
newsletter preprocess
newsletter tag
newsletter classify
newsletter trend --topic technology
newsletter trend --topic backend
newsletter trend --topic qa
newsletter edit --topic technology
newsletter edit --topic backend
newsletter edit --topic qa
newsletter publish --topic technology
newsletter publish --topic backend
newsletter publish --topic qa
newsletter run-all
```

The CLI surface is defined before all stages are implemented. Current commands
are architectural contracts, and future sessions should replace each command
stub with one bounded stage implementation.

## Setup

```bash
uv sync --extra dev
cp .env.example .env
```

AI provider:

- Gemini API via `google-genai`
- Default model: `gemini-2.0-flash`

Required secrets:

```text
GEMINI_API_KEY=
DISCORD_WEBHOOK_TECHNOLOGY=
DISCORD_WEBHOOK_BACKEND=
DISCORD_WEBHOOK_QA=
```

## Development

```bash
uv run pytest
```

Local smoke test:

```bash
uv sync --extra dev
newsletter --help
newsletter --data-dir data preprocess
newsletter --data-dir data classify
```

`tag`, `trend`, and `edit` call Gemini API and require `GEMINI_API_KEY`.
`publish` requires a Discord Webhook environment variable for the selected
topic.

## Automation

The project should be runnable from GitHub Actions Cron or a local scheduler.
The preferred first production target is GitHub Actions Cron because this keeps
the application stateless and CLI-first.
