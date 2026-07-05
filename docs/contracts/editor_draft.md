# Editor Draft Contract

This contract defines the internal boundary between AI editorial generation and
deterministic newsletter rendering.

The public Editor stage contract remains unchanged:

```text
<topic>_trends.json + <topic>_articles.json -> <topic>_newsletter.md
```

The Editor must not ask an AI model to emit Markdown links or URLs. The model
produces a structured draft containing prose and article IDs. Runtime code
resolves those IDs against `<topic>_articles.json` and renders the final
Markdown.

## Data Flow

```text
Collector articles.json
  -> Preprocessor canonical_url and stable article ID
  -> Tagger and Classifier preserve both values
  -> Trend Agent selects article IDs
  -> Editor AI writes prose and selects article IDs
  -> deterministic renderer inserts title and canonical_url
  -> <topic>_newsletter.md
```

The Collector and Preprocessor remain the only source of publishable article
URLs. Dynamic collection is preserved because the renderer reads the current
run's topic article file instead of a static URL table.

## Intermediate Artifact

The validated draft may be stored at:

```text
data/.state/editor/<topic>/<run_id>/editor_draft.json
```

It must validate against `editor_draft.schema.json`. The artifact is private to
the Editor stage. Publisher and other stages must consume only the final
newsletter Markdown.

## AI Input

The model receives:

- topic and period
- trend IDs and editorial metadata
- article IDs, titles, summaries, and other non-URL metadata needed for writing
- explicit output schema and length limits

The model must not receive `canonical_url`, `original_url`, or another URL
field. Removing URLs from the prompt prevents accidental copying, mutation, and
reconstruction from being part of the model's responsibility.

The runtime retains the complete `<topic>_articles.json` document outside the
model context for validation and rendering.

## AI Output

The model returns JSON only. It may produce:

- Korean newsletter title text
- Korean summary items
- one editorial section per selected trend
- Korean overview and why-it-matters prose
- article IDs belonging to that trend
- Korean weekly insight items

The model must not produce:

- URLs or URL-like Markdown destinations
- article titles to be used as link labels
- Markdown syntax, HTML, links, or autolinks
- emoji, section markers, list markers, or heading markers
- unknown trend IDs or article IDs
- deterministic fields such as timestamps, fingerprints, or model names

Runtime code supplies the envelope fields `schema_version`, `topic`, and
`generated_at` after validating the model response.

### Plain-text Invariant

Every AI-authored prose field is plain text. AI output contains editorial
meaning only; it never contains presentation syntax. In particular:

- `title` and `heading` are trimmed, non-empty, single-line strings.
- `title` and `heading` must not start with or contain Markdown heading markers,
  list markers, HTML, links, or emoji.
- `summary_items`, `overview`, `why_it_matters`, and `insight_items` must not
  contain Markdown headings, Markdown lists, HTML, links, or emoji.
- A newline used to introduce a Markdown block is prohibited. The renderer,
  rather than model prose, owns document structure.

The title value is the human-readable title only:

```text
주간 QA 및 품질 엔지니어링 리포트
```

These values are invalid:

```text
# 🗞️ 주간 QA 및 품질 엔지니어링 리포트
🗞️ 주간 QA 및 품질 엔지니어링 리포트
# 주간 QA 및 품질 엔지니어링 리포트
[주간 QA 리포트](https://example.com)
```

JSON Schema provides structural and basic lexical checks. Runtime validation
must additionally perform Unicode-aware emoji detection and reject prohibited
Markdown block syntax. Passing the schema alone is not sufficient acceptance.

## Reference Validation

Before storing or rendering a draft, runtime code must enforce all of the
following:

1. Every `trend_id` exists in the input `<topic>_trends.json`.
2. Every trend appears at most once.
3. Every `article_id` exists in the input `<topic>_articles.json`.
4. Every `article_id` belongs to the referenced trend's `article_ids` list.
5. Article IDs are unique within each trend section.
6. All prose fields contain no HTTP(S) URL, Markdown link destination, HTML
   link, or autolink.
7. All prose fields satisfy the plain-text invariant, including Unicode-aware
   emoji and Markdown block-syntax rejection.
8. The response satisfies `editor_draft.schema.json` and configured size
   limits.

Unknown or misplaced IDs are content-validation failures. They must never be
silently mapped to a similar ID or URL.

## Deterministic Rendering

The renderer builds Markdown without AI calls:

- the H1 marker, title emoji, and fixed section headings come from the
  newsletter contract
- trend numbering, importance emoji, list markers, and minor labels are
  selected deterministically by runtime code
- prose comes from the validated draft
- trend ordering follows the draft after validating it against input trends
- each `article_id` is looked up in `<topic>_articles.json`
- link text uses the stored article `title`
- link destination uses the stored `canonical_url` byte-for-byte
- Markdown-sensitive link-label characters are escaped deterministically
- missing or duplicate references fail rendering

Conceptually:

```text
article_id
  -> topic_articles[article_id]
  -> "- 🔗 [<escaped title>](<canonical_url>)"
```

The renderer must not normalize, guess, repair, redirect, or synthesize a URL.
URL canonicalization remains the Preprocessor's responsibility.

The renderer is the exclusive owner of all Markdown and emoji. It must not
preserve, strip, or repair presentation syntax received from AI because such a
draft must already have failed validation. For a valid title, rendering is
exactly:

```text
draft.title = "주간 QA 및 품질 엔지니어링 리포트"
rendered H1 = "# 🗞️ 주간 QA 및 품질 엔지니어링 리포트"
```

## Final Validation

After rendering, the existing newsletter contract still applies. The Editor
must validate required sections, Korean body content, Discord length, and the
absence of unsupported Markdown constructs.

Final shape validation must also require:

- exactly one level-one heading
- the first line matches `^# 🗞️ [^#\r\n]+$`
- the title emoji `🗞️` occurs exactly once in the document
- each required level-two section occurs exactly once
- no AI prose value becomes an ATX heading or Markdown list block

As a defense in depth check, every URL extracted from the final Markdown must
exactly match a `canonical_url` selected through a validated article ID. A URL
validation failure indicates a renderer or escaping defect, not a recoverable
AI content error.

## Retry And Recovery

- Invalid JSON, schema violations, unknown IDs, misplaced IDs, and prohibited
  URL, Markdown, HTML, or emoji content are recoverable AI content failures.
- Retry once with validation feedback containing IDs only; do not add URLs to
  the corrective prompt.
- The configured fallback model may be attempted after retry exhaustion.
- Persist only fully validated drafts.
- Never replace an existing valid final newsletter after generation or
  rendering failure.

## Fingerprints And Resume

The Editor policy fingerprint must cover:

- this contract and `editor_draft.schema.json`
- topic prompt content
- model and generation configuration
- renderer version or renderer policy identity
- newsletter output contract

A stored draft is reusable only when topic, input fingerprints, and policy
fingerprint match the current run.

## Required Conformance Cases

Implementations must include regression tests proving:

```text
title "# 🗞️ 제목"       -> reject
title "🗞️ 제목"         -> reject
title "# 제목"           -> reject
title "제목\n부제"       -> reject
title "[제목](...)"      -> reject
title "<b>제목</b>"      -> reject
title "정상적인 제목"    -> render as "# 🗞️ 정상적인 제목"
```

The governing invariant is:

```text
AI output contains no presentation syntax.
Only renderer output contains Markdown, emoji, article titles, and URLs.
```
