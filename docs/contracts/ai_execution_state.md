# AI Execution State Contract

This contract shares Gemini model health within one pipeline run.

## Storage

```text
data/.state/ai/<run_id>/execution_state.json
```

The file must validate against `ai_execution_state.schema.json` and be written
atomically. It is internal state, not a downstream stage input.

## Circuit Rules

- Start with the circuit `closed` and the primary model preferred.
- Open the circuit only after retryable 429 or 5xx failures exhaust the primary
  model policy and fallback succeeds.
- While open, Tagger, Trend, and Editor use fallback first for the rest of the
  run and for a resumed run with the same policy fingerprint.
- Authentication, validation, malformed output, and programming errors do not
  open the circuit.
- A new run or changed policy fingerprint starts with a closed circuit.

## Invariants

- `preferred_model` must equal the primary model while closed and fallback
  model while open.
- State updates happen only after an observed model outcome.
- The actual accepted model remains recorded in every stage-specific artifact.
- API keys, prompts, request bodies, and raw responses must never be stored.
