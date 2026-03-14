# Branch Handoff: `feat/revisions-and-change-tokens`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/02-stack-and-codebase-structure.md
- /codex-agent-plan/docs/03-data-model-and-mutability.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/revisions-and-change-tokens

## Objective

Implement revision snapshots and change-token handling so contract-affecting service changes can be tracked explicitly and later flows can detect stale clients and stale quotes.

## In scope

- `service_revisions` model/table owned by this branch
- revision snapshot persistence
- change-token generation
- classification of material vs non-material changes
- service-layer logic for creating revisions when contract-affecting fields change
- tests for revision and change-token behaviour

## Required implementation details

- follow the mutability policy in the planning docs
- focus on contract-affecting service fields rather than every possible metadata edit
- keep the implementation reusable by later publish, quote, and invoke branches
- keep names short and meaningful
- keep route handlers thin if any route-level changes are necessary
- do not add unrelated refactors

## Out of scope

- pricing logic beyond what is needed to snapshot contract state
- publish activation flow
- public discovery routes
- quote flow
- invoke flow
- x402 integration
- ledger logic

## Tests required

- revision creation tests
- change-token generation tests
- material vs non-material change classification tests
- persistence tests for revision snapshots

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. revision model and persistence
2. change-token and material-change logic
3. integration into service update paths where needed
4. tests and cleanup

## Acceptance criteria

- contract-affecting changes produce revision snapshots
- change tokens are generated and updated correctly
- non-material changes do not create unnecessary revisions
- tests pass
- no quote/invoke/payment logic is introduced

## Report back with

- summary of changes
- files changed
- migrations added
- tests run
- any follow-up tasks or blockers
