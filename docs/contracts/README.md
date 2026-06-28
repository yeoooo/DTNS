# JSON Contracts

Contracts define the file interfaces between stages.

All JSON files should use UTF-8, two-space indentation, and ISO 8601 timestamps.
Arrays should preserve ranking order where order is meaningful.

Schema files:

- `articles.schema.json`: raw collector output.
- `normalized_articles.schema.json`: preprocessor output.
- `tagged_articles.schema.json`: tagger output.
- `topic_articles.schema.json`: classifier output for one topic.
- `trends.schema.json`: trend agent output for one topic.

Markdown contract:

- `newsletter.md`: editor output expectations.

External boundary contract:

- `discord_delivery.md`: Discord Webhook retry and terminal failure rules.

Internal processing contracts:

- `tagger_batch_processing.md`: adaptive Tagger batching, checkpointing, and
  resume rules.
- `tagger_batch_checkpoint.schema.json`: completed Tagger batch checkpoint
  format.
- `trend_batch_processing.md`: bounded Trend Map-Reduce, checkpointing, and
  recovery rules.
- `trend_candidate_checkpoint.schema.json`: Trend Map and Reduce candidate
  checkpoint format.
- `ai_execution_state.md` and `ai_execution_state.schema.json`: shared Gemini
  circuit state for one pipeline run.
- `editor_generation_checkpoint.md` and
  `editor_generation_checkpoint.schema.json`: validated Markdown candidate
  checkpoint.
- `publish_receipt.md` and `publish_receipt.schema.json`: Discord chunk delivery
  receipt and duplicate prevention.
- `collection_report.md` and `collection_report.schema.json`: source-level
  Collector health report.
- `pipeline_run.md` and `pipeline_run.schema.json`: stage status, artifact
  fingerprints, and run-all resume rules.
