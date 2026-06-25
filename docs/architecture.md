# Architecture

DTNS is a Python 3.12 CLI application that generates weekly engineering
newsletters and publishes them to Discord Webhooks.

The architecture optimizes for independent implementation by future Codex
sessions. Each stage owns one responsibility, communicates through a file
contract, and can be executed from the CLI without requiring the rest of the
pipeline to be in memory.

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
Technology Trend Agent
  -> technology_trends.json
Technology Editor Agent
  -> technology_newsletter.md
Publisher
  -> Technology Discord Webhook

Backend Trend Agent
  -> backend_trends.json
Backend Editor Agent
  -> backend_newsletter.md
Publisher
  -> Backend Discord Webhook

QA Trend Agent
  -> qa_trends.json
QA Editor Agent
  -> qa_newsletter.md
Publisher
  -> QA Discord Webhook
```

## Data Directory

The default runtime data directory is `data/`. It is intentionally ignored by
Git because generated articles, trends, and newsletters are build artifacts.

Expected filenames:

| Stage | Output |
| --- | --- |
| Collector | `articles.json` |
| Preprocessor | `normalized_articles.json` |
| Tagger Agent | `tagged_articles.json` |
| Classifier | `technology_articles.json`, `backend_articles.json`, `qa_articles.json` |
| Trend Agent | `technology_trends.json`, `backend_trends.json`, `qa_trends.json` |
| Editor Agent | `technology_newsletter.md`, `backend_newsletter.md`, `qa_newsletter.md` |

## Topics

The supported topic identifiers are:

- `technology`
- `backend`
- `qa`

Classification is multi-label. A single article can appear in multiple topic
article files.

## Stage Responsibilities

### Collector

No AI.

Collects raw article candidates from feeds and APIs such as InfoQ, OSS Insight,
GitHub Releases, engineering blogs, and official project blogs.

The collector should preserve source metadata and avoid editorial judgment.

### Preprocessor

No AI.

Normalizes article fields, removes duplicates, cleans URLs, assigns stable IDs,
and validates required fields.

Stable IDs should be deterministic from canonical URL when available.

### Tagger Agent

Uses AI.

Reads normalized articles and attaches technologies, domains, technical tags,
summary metadata, and confidence values. The agent writes JSON only.

### Classifier

Prefer deterministic logic.

Reads tagged articles and writes three topic files. Classification rules should
be transparent and testable. Use tags, domains, known technology maps, and source
metadata before considering any future AI fallback.

### Trend Agent

Uses AI.

Runs independently for each topic. It clusters articles, discovers weekly
trends, assigns importance, and writes trend JSON. It does not generate
Markdown and does not publish.

### Editor Agent

Uses AI.

Runs independently for each topic. It writes Korean Markdown newsletters from
trend JSON and topic articles. It explains trends, summarizes key articles,
states why they matter, and adds weekly insights without fully translating
source articles.

### Publisher

No AI.

Publishes topic newsletter Markdown to Discord Webhooks. It must split messages
that exceed Discord limits and avoid modifying editorial content except for safe
message splitting.

## CLI Commands

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

All commands should accept an optional `--data-dir` argument. Topic-specific
commands should accept `--topic`.

## Source Layout

```text
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

The `cli.py` module should orchestrate stages by command. Stage modules should
expose small entry points that receive input and output paths.
