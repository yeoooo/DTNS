# Content Selection Contract

This contract defines the intermediate signals used to optimize DTNS for
signal-to-noise ratio without merging stage responsibilities.

## Compatibility

The artifact envelope remains schema version `1.0`. The new fields are additive
and have runtime defaults so existing artifacts remain replayable. New pipeline
runs must populate source metadata at collection and content signals at tagging.

## Source provenance

`source_type` on an article remains the collector transport (`rss`, `atom`,
`github_release`, `api`, or `html`). Editorial provenance is carried separately
in `source_metadata`:

- `source_type`: `official_release`, `engineering_blog`, `developer_blog`,
  `engine_case_study`, `conference`, `research_lab`, `steam_devlog`,
  `technical_media`, `technical_synthesis`, or `discovery`.
- `source_priority`: `high`, `medium`, or `low`.
- `source_name` and `source_url`: the configured origin, not an aggregator link.
- `is_discovery`: whether the source is primarily for discovering direct sources.

The Collector assigns these values deterministically. Discovery items may enter
the candidate pool, but downstream stages prefer a direct primary item when both
cover the same evidence. Automatic URL guessing or unverified source replacement
is forbidden.

## Tagger output

The Tagger describes technical content and never assigns a newsletter topic or
makes the final include/exclude decision. It emits:

- `technical_topics` and concrete `technologies`.
- `article_type`, including `production_case`, `release`,
  `technical_synthesis`, and `architecture_essay`.
- evidence flags for implementation detail, metrics, trade-offs, production
  problems, prior limits, cross-source synthesis, explanation, and decision
  criteria.
- nine integer evaluation scores from 0 through 3.
- `release_change_types` describing meaningful or low-signal release content.

The runtime derives `evaluation.source_quality` from `source_priority` (3/2/1),
so an AI response cannot promote a weak source by itself.

## Classifier selection

The Classifier first performs transparent multi-label topic matching and then
applies deterministic selection rules. It records `selection_score`, `selected`,
and stable `rejection_reasons` in `classification`.

- Meaningful releases require at least one of breaking change, major feature,
  performance improvement, architecture change, new programming model, new
  runtime capability, developer experience improvement, or critical security.
- Patch-only, bug-fix-only, documentation-only, and dependency-only releases are
  rejected unless another meaningful change is present.
- Low-technical-signal announcements and general news are rejected.
- Game maps, characters, items, skins, events, battle passes, and balance patches
  are rejected when implementation detail is absent.
- Strong production evidence and strong synthesis evidence use a lower threshold
  than generic articles, but still require matching topic rules.

Only selected articles are written to topic files. One article may be selected
for multiple topics.

## Trend evidence roles

Each Trend keeps `article_ids` and may additionally assign each article one role:

- `production_case`
- `meaningful_release`
- `technical_perspective`
- `supporting`

Roles must reference the same Trend's article IDs. The Trend Agent seeks a shared
movement rather than a list of events and uses technical perspectives as context
that explains production cases and releases. Missing role categories are allowed
when the week's evidence does not support them; the agent must not fabricate a
balanced set.

## Editor boundary

The Editor receives source priority, article evaluation, evidence, and Trend
roles. It writes Korean editorial prose in this order: what happened, the shared
pattern, why it is happening, and what it changes for engineering decisions.
It does not give every article equal weight and does not invent details missing
from upstream artifacts.

