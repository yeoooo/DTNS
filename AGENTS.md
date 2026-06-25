# Codex Project Instructions

## Commit Messages

아래 형식에 맞게 커밋 메세지를 남긴다.

- tag: `feat`, `fix`, `chore`
- content: 커밋 내용

```text
[tag] content
```

Example:

```text
[chore] define newsletter architecture contracts
```

## Project Role

This repository is the architecture root for an AI-powered engineering
newsletter CLI. Future Codex CLI sessions should implement one bounded module at
a time.

Do not implement the full application in a single session. Prefer defining or
honoring contracts, module boundaries, and tests for the specific module being
worked on.

## Architecture Rules

- The application is CLI-first.
- Pipeline stages communicate through files only.
- A stage must not import another stage's implementation to exchange runtime
  state.
- Contracts live in `docs/contracts/` and implementation-facing models live in
  `src/dtns/contracts/`.
- AI is allowed only for tagging, trend discovery, and editorial writing.
- Deterministic work such as collection, preprocessing, classification, and
  publishing must not use AI.
- Each newsletter topic must be independently executable.

## Module Boundaries

- `collectors`: fetch article candidates and write `articles.json`.
- `preprocessors`: normalize, deduplicate, clean URLs, assign stable IDs, and
  write `normalized_articles.json`.
- `agents/tagger`: enrich normalized articles with technologies, domains, and
  tags using AI.
- `classifier`: deterministic multi-label classification into newsletter
  topics.
- `agents/trend`: discover topic-specific weekly trends using AI and emit JSON.
- `agents/editor`: generate Korean newsletter Markdown using AI.
- `publisher`: publish Markdown to Discord Webhooks and split long messages.
- `cli`: command surface and orchestration only.

## Prompt Rules

- Store prompt templates in `src/dtns/prompts/`.
- Keep one prompt per AI responsibility.
- Do not embed large prompt strings directly in Python modules.
- Prompts must require JSON output for tagger and trend agents.
- Editor prompts must generate Korean Markdown and keep technical names in
  English.

## Implementation Guidance

- Use Python 3.12 and `uv`.
- Use Gemini API for AI stages. Do not introduce another LLM provider SDK unless
  the project explicitly changes providers again.
- Prefer `pydantic` models for contract validation.
- Prefer `httpx` for HTTP.
- Prefer explicit file paths and stage output names from `docs/architecture.md`.
- Add focused tests for contract validation and deterministic rules.
- Keep changes scoped to the module requested by the user.
