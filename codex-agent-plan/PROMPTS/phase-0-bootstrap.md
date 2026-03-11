# Branch Handoff: `feat/project-bootstrap`

## Read first

- AGENTS.md
- README.md
- codex-agent-plan/README.md
- codex-agent-plan/docs/00-repo-bootstrap-contract.md
- codex-agent-plan/docs/10-definition-of-done-by-branch.md
- codex-agent-plan/PROMPTS/phase-0-bootstrap.md

## Branch

- `feat/project-bootstrap`

## Objective

Create the repository bootstrap for a FastAPI backend using uv and Python 3.12. Set up the project structure, health endpoint, testing baseline, linting, typing, and CI. Do not implement domain-specific business features.

## In scope

- uv-based Python project setup
- `.python-version` with Python 3.12
- `pyproject.toml`
- FastAPI app skeleton
- health route
- pytest baseline
- Ruff config
- mypy config
- basic CI for lint/type/test
- initial folder structure from the bootstrap contract

## Out of scope

- service marketplace logic
- DB-heavy business domain implementation beyond the baseline needed to support startup
- x402 integration
- quote flow
- invoke flow
- ledger logic
- moderation enforcement logic

## Required implementation details

- Use the folder structure from `codex-agent-plan/docs/00-repo-bootstrap-contract.md`.
- Keep route handlers thin.
- Add a health route that can be used in smoke tests.
- Set up Ruff with the required rule families.
- Set up mypy for `app`.
- Set up pytest and basic test discovery.
- Set up CI to run lint, type checks, and tests.

## Tests required

- app startup smoke test
- health route test
- settings/config smoke test if config is included in this branch

## Commit guidance

Preferred commit split:

1. uv and project metadata
2. FastAPI app skeleton and folder structure
3. Ruff and mypy config
4. pytest and health route tests
5. CI

Keep commits small and reviewable.

## Acceptance criteria

- project installs and syncs with uv
- app starts
- health route works
- tests run
- Ruff runs cleanly
- mypy runs on `app`
- CI config is present

## Report back with

- summary of changes
- files changed
- tests run
- any follow-up tasks or blockers
