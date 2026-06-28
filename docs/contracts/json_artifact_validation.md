# JSON Artifact Validation Contract

This contract defines how pipeline stages and orchestration code must validate
JSON artifacts read from disk. It applies to public stage outputs and private
checkpoint files that use Pydantic models.

## Goals

- Preserve strict contract validation without rejecting valid JSON date and
  datetime strings.
- Keep validation behavior consistent between stage consumers, pipeline output
  checks, and resume logic.
- Prevent an artifact from passing one validation path and failing another.

## File Boundary Rule

Persisted JSON must be validated from its original UTF-8 JSON representation.
For a Pydantic contract model, consumers must use `model_validate_json` with
the file bytes:

```python
document = ContractModel.model_validate_json(path.read_bytes())
```

Consumers must not decode the artifact with `json.loads` and then pass the
result to `model_validate` when the contract or any nested model uses
`strict=True`:

```python
# Prohibited for strict persisted contracts.
document = ContractModel.model_validate(json.loads(path.read_bytes()))
```

JSON represents dates and datetimes as strings. Pydantic's strict JSON
validation mode accepts schema-valid ISO 8601 strings for typed `date` and
`datetime` fields. Strict Python-object validation receives ordinary `str`
objects after `json.loads` and rejects their conversion.

## Strictness Policy

- Do not remove `strict=True` to work around a file-loading failure.
- Do not add permissive field validators that duplicate Pydantic's JSON date
  or datetime parsing.
- Keep `extra="forbid"`, literal values, bounds, and nested strict models
  active at file boundaries.
- Use `model_validate` for already-typed in-memory Python objects only.
- Use `model_validate_json` for JSON bytes or JSON text read from artifacts.

## Temporal Fields

- Datetimes must use ISO 8601 and include a timezone offset or `Z` when the
  model requires timezone awareness.
- Dates must use the `YYYY-MM-DD` calendar-date format.
- Writers must serialize models in JSON mode, for example with
  `model_dump(mode="json")` or `model_dump_json()`.
- Readers must not pre-convert, truncate, or normalize temporal strings before
  contract validation.

## Pipeline Validation

Pipeline output validators must validate the exact bytes written by the stage
before recording the stage as completed. Topic-specific semantic checks, such
as matching the requested topic, run only after JSON contract validation.

For Trend output validation, the required sequence is:

1. Read `<topic>_trends.json` as bytes.
2. Validate it with `TrendsFile.model_validate_json`.
3. Confirm that `document.topic` matches the requested pipeline topic.
4. Record the artifact fingerprint and mark the stage completed.

A validation failure must leave the pipeline stage in `failed` state and must
not discard valid Trend checkpoints. A later `run-all` execution may resume
from those checkpoints.

## Error Contract

Validation errors should identify the artifact path, contract name, and failed
field without including full artifact contents. Errors must not include API
keys, webhook URLs, prompts, or raw model responses.

## Required Tests

Every strict persisted contract containing temporal fields must cover:

- an ISO 8601 datetime with a `Z` suffix;
- date fields encoded as `YYYY-MM-DD` JSON strings;
- rejection of malformed dates and datetimes;
- rejection of unexpected fields and invalid literal values;
- semantic checks that run after successful JSON parsing, such as a topic
  mismatch.

The Trend pipeline validator must include a regression test using the exact
serialized shape produced by the Trend Agent.
