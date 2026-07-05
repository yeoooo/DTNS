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
- Markdown links, HTML links, or autolinks
- unknown trend IDs or article IDs
- deterministic fields such as timestamps, fingerprints, or model names

Runtime code supplies the envelope fields `schema_version`, `topic`, and
`generated_at` after validating the model response.

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
7. The response satisfies `editor_draft.schema.json` and configured size
   limits.

Unknown or misplaced IDs are content-validation failures. They must never be
silently mapped to a similar ID or URL.

## Deterministic Rendering

The renderer builds Markdown without AI calls:

- fixed section headings come from the newsletter contract
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

## Final Validation

After rendering, the existing newsletter contract still applies. The Editor
must validate required sections, Korean body content, Discord length, and the
absence of unsupported Markdown constructs.

As a defense in depth check, every URL extracted from the final Markdown must
exactly match a `canonical_url` selected through a validated article ID. A URL
validation failure indicates a renderer or escaping defect, not a recoverable
AI content error.

## Retry And Recovery

- Invalid JSON, schema violations, unknown IDs, misplaced IDs, and prohibited
  URL content are recoverable AI content failures.
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
