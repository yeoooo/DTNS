# Collectors

Collector implementations fetch raw article candidates and write
`articles.json`.

Rules:

- No AI.
- Preserve source metadata.
- Do not deduplicate beyond obvious fetch-level duplication.
- Do not classify or summarize beyond source-provided summaries.

Expected sources:

- InfoQ
- OSS Insight
- GitHub Releases
- GitHub Trending (weekly)
- Engineering blogs
- Official project blogs
- Game engine release feeds and game client development sources
- GDC Vault and Advances in Real-Time Rendering HTML indexes
- Hugging Face Blog
- LinkedIn Engineering Feed and Inside.java
- X accounts configured through the official API when `X_BEARER_TOKEN` is set
