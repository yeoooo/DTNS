# Publish Receipt Contract

This contract provides best-effort duplicate prevention for Discord publishing.

## Storage

```text
data/.state/publisher/<topic>/<newsletter_fingerprint>.<webhook_fingerprint>.json
```

The receipt validates against `publish_receipt.schema.json` and is updated
atomically after every confirmed chunk delivery.

## Identity

- Newsletter fingerprint is SHA-256 of the exact Discord delivery bytes. These
  are the Markdown bytes with any deterministic publish label prepended.
- A manual test-publication label therefore has a different receipt identity
  from an unlabeled scheduled publication of the same newsletter.
- Webhook fingerprint is SHA-256 of the normalized webhook URL.
- The webhook URL itself must never be persisted.
- Chunk fingerprints are calculated after deterministic Discord splitting.

## Delivery Rules

- Create all pending chunk records before sending the first chunk.
- Send chunks in index order and follow `discord_delivery.md` retry rules.
- Mark a chunk delivered only after a successful Discord response.
- On resume, skip delivered chunks only when newsletter, webhook, index, chunk
  hash, and character count all match.
- Do not automatically retry an `unknown` chunk because a network timeout may
  have occurred after Discord accepted it.
- Mark the receipt completed only when every chunk is delivered.
- A new newsletter or webhook fingerprint creates a new receipt.

Discord does not provide a general idempotency key for webhook message sends,
so this contract cannot guarantee exactly-once delivery across ambiguous
network failures. It prevents deterministic reruns of confirmed chunks.
