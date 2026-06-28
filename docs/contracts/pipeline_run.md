# Pipeline Run Manifest Contract

This contract coordinates stage-level resume for `newsletter run-all`.

## Storage

```text
data/.state/pipeline/<run_id>/pipeline_run.json
```

The manifest validates against `pipeline_run.schema.json` and is updated
atomically before and after every stage transition.

## State Transitions

```text
pending -> running -> completed
                   -> failed
pending -> skipped
failed  -> running
```

- A pipeline is completed only when every required stage is completed or
  explicitly skipped by policy.
- Start a stage only after all required predecessor artifacts validate.
- Increment attempts whenever a stage enters running.
- Store artifact SHA-256 fingerprints, not artifact contents.

## Resume Rules

- Revalidate completed stage outputs and fingerprints before skipping work.
- Resume from the first invalid, failed, or incomplete stage.
- AI checkpoints remain owned by Tagger, Trend, and Editor contracts.
- A publish stage may be skipped only when a matching completed publish receipt
  exists.
- Do not infer successful Discord delivery from a newsletter file alone.
- Changed configuration or inputs invalidate the affected stage and all of its
  dependents.

## GitHub Actions Persistence

- Upload the `.state` directory as an artifact even when the workflow fails.
- A resumed workflow must explicitly download state from the selected prior
  run; it must never select arbitrary or untrusted artifacts automatically.
- Validate every downloaded state file before use.
- State artifacts must not contain `.env`, API keys, webhook URLs, prompts,
  cookies, authorization headers, or raw exception bodies.
