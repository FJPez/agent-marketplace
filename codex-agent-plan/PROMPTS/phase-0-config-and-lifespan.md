# Branch Handoff: `feat/config-and-lifespan`

## Read first

- `AGENTS.md`
- `README.md`
- `codex-agent-plan/README.md`
- `codex-agent-plan/docs/00-repo-bootstrap-contract.md`
- `codex-agent-plan/docs/02-stack-and-codebase-structure.md`
- `codex-agent-plan/docs/10-definition-of-done-by-branch.md`
- `codex-agent-plan/PROMPTS/phase-0-config-and-lifespan.md`

## Branch

- `feat/config-and-lifespan`

## Objective

Implement typed application settings and FastAPI lifespan wiring for shared resources. This branch should establish the project’s configuration contract and startup/shutdown structure without adding domain behaviour.

## In scope

- typed settings using `pydantic-settings`
- settings module under `app/core/config.py`
- environment-aware settings loading
- lifespan setup under `app/core/lifespan.py`
- application wiring so shared resources can be created and cleaned up centrally
- placeholders or scaffolding for shared clients/resources the app will later use
- tests for settings loading and app startup with lifespan enabled

## Out of scope

- domain routes
- business services
- x402 integration logic
- database models beyond what is strictly needed for startup wiring
- quote, invoke, ledger, moderation, or discovery logic

## Required implementation details

- use Python 3.12 and uv conventions already defined in the repo bootstrap contract
- keep settings typed and explicit
- avoid hard-coding environment values in application code
- use FastAPI lifespan rather than scattered startup logic
- keep the implementation small and clean so later branches can attach DB, HTTP clients, and telemetry to it

## Tests required

- settings parse smoke test
- app startup/lifespan smoke test
- environment override test for at least one setting

## Commit guidance

Preferred commit split:

1. settings models and environment contract
2. lifespan wiring and application integration
3. tests and minor cleanup

## Acceptance criteria

- app can start with typed settings loaded
- lifespan is present and wired into the FastAPI app
- tests pass
- no unrelated domain logic is introduced

## Report back with

- summary of changes
- files changed
- tests run
- follow-up tasks or blockers
