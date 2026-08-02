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
- QA publications and communities
