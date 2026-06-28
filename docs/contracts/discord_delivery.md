# Discord Delivery Boundary Contract

This contract defines how the deterministic Publisher handles one Discord
Webhook message chunk. It is an internal HTTP boundary contract and does not
add another pipeline artifact.

## Input

- One non-empty Markdown chunk of at most 2,000 characters.
- One resolved Discord Webhook URL.
- `allowed_mentions.parse` must be empty.

## Outcomes

| Response | Outcome |
| --- | --- |
| HTTP below 400 | The chunk is delivered. Continue with the next chunk. |
| HTTP 429 | Retry the same chunk after Discord's requested delay. |
| HTTP 500-599 | Retry the same chunk with exponential backoff. |
| Network error | Retry the same chunk with exponential backoff. |
| Other HTTP 400-499 | Terminal failure. Do not retry. |

## Retry Rules

- A chunk is attempted at most five times, including the initial request.
- For HTTP 429, use the JSON `retry_after` field first, then the `Retry-After`
  header. Values are seconds and may be fractional.
- Add a 50 ms buffer to a valid Discord rate-limit delay.
- If Discord omits a usable delay, use exponential delays of 1, 2, 4, and 8
  seconds.
- HTTP 5xx and network errors use the same exponential schedule.
- After the final attempt, raise `DiscordPublishError` with the attempt count,
  HTTP status when available, and response body when available.

## Delivery Invariants

- Process chunks in file order.
- Retry only the current failed chunk.
- Never resend chunks that already returned a successful response.
- Never retry authorization, permission, validation, or missing webhook errors.
- The Publisher remains deterministic and must not use AI.
