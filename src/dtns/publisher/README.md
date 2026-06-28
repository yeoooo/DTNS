# Publisher

The publisher reads `<topic>_newsletter.md` and publishes it to the matching
Discord Webhook.

Rules:

- No AI.
- Use `DISCORD_WEBHOOK_TECHNOLOGY`, `DISCORD_WEBHOOK_BACKEND`, and
  `DISCORD_WEBHOOK_QA`.
- Split messages when required by Discord limits.
- Preserve Markdown content except for safe splitting.
- Follow `docs/contracts/discord_delivery.md` for HTTP retry behavior.
