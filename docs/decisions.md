# Architecture Decisions

## ADR-001: File-Based Stage Contracts

Decision: Every pipeline stage communicates through files.

Reasoning: This makes the pipeline easy to inspect, rerun, cache, and implement
independently. It also lets future Codex sessions work on one module without
needing unrelated modules to be complete.

Trade-off: File IO adds some ceremony compared with in-memory orchestration.
The benefit is clearer boundaries and easier debugging.

## ADR-002: AI Only for Reasoning Tasks

Decision: AI is limited to tagging, trend discovery, and editorial writing.

Reasoning: Collection, preprocessing, classification, and publishing are
deterministic workflows. Keeping AI out of those stages makes behavior more
testable and cheaper to run.

Trade-off: Deterministic classification requires maintaining rule maps. That is
acceptable because the topic taxonomy is small and should be transparent.

## ADR-003: Multi-Label Classification

Decision: Topic classification is multi-label.

Reasoning: Engineering articles frequently cross boundaries. OpenTelemetry can
belong to both technology trends and backend. Testcontainers can belong to both
backend and QA.

Trade-off: Newsletters may share some articles. Editors should frame shared
articles differently for each audience.

## ADR-004: GitHub Actions Cron as First Automation Target

Decision: Prefer GitHub Actions Cron before adding a persistent scheduler.

Reasoning: The app is CLI-first and stateless. Cron execution in GitHub Actions
is simpler for open-source operation than managing a long-running process.

Trade-off: GitHub Actions has runtime limits and depends on repository secrets.
APScheduler can be added later for self-hosted deployments.

## ADR-005: Prompt Files Outside Python Code

Decision: Prompt templates live in `src/dtns/prompts/`.

Reasoning: Prompts are contracts for AI behavior and should be reviewable
without reading implementation code.

Trade-off: Runtime prompt loading needs path handling. This is small compared
with the maintainability gain.
