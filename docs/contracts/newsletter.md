# Newsletter Markdown Contract

Editor agents write one Markdown file per topic:

- `technology_newsletter.md`
- `backend_newsletter.md`
- `qa_newsletter.md`

## Requirements

- Language: Korean.
- Technical names remain in English.
- Use a restrained set of emojis to improve Discord readability.
- Use emojis as section markers, not as decoration in every sentence.
- The newsletter explains trends instead of fully translating source articles.
- Each cited article must include its original URL.
- The output must be suitable for Discord message publishing.
- Do not include hidden metadata or JSON front matter.

## Suggested Shape

```markdown
# 🗞️ 이번 주 <Topic> 뉴스레터

## 🔎 핵심 요약

- ...

## 📌 주요 트렌드

### 1. 🚀 <Trend Title>

...

**왜 중요한가**
- ...

**관련 글**
- 🔗 [Article Title](https://example.com)

## 💡 이번 주 인사이트

...
```

Recommended emoji vocabulary:

- `🗞️` newsletter title
- `🔎` summary
- `📌` trend section
- `🚀` high importance trend
- `🧭` medium importance trend
- `🔧` implementation or operational note
- `🧪` QA/testing note
- `🔗` article links
- `💡` weekly insight
- `⚠️` caution or risk
