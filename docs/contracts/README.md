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
