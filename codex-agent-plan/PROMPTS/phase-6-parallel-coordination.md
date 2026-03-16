# Parallel Coordination Handoff: Phase 6

Read these files first:

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/05-x402-integration.md
- /codex-agent-plan/docs/06-branch-and-workstream-plan.md
- /codex-agent-plan/docs/07-phase-by-phase-implementation.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md
- /codex-agent-plan/PROMPTS/phase-6-ledger-and-earnings.md
- /codex-agent-plan/PROMPTS/phase-6-platform-guardrails.md

You are the orchestration agent for Phase 6.

Your job is to launch and coordinate 2 separate implementation agents running in parallel on separate branches. You are not the primary implementation owner of either branch.

Use 2 isolated implementation agents, each working only on its assigned branch.

## Planning-first workflow

Before any implementation begins, first spawn a separate planning agent for each branch.

Each planning agent must:

- work only in plan mode
- read the relevant branch prompt and supporting docs
- inspect the current repo state
- identify expected file touchpoints
- identify likely merge overlap with other active branches
- identify blockers, ambiguities, or missing assumptions
- produce a short branch-specific implementation plan

Do not start coding immediately.

If any planning agent has questions, unclear lifecycle assumptions, schema concerns, or branch-boundary uncertainty, forward those questions to me before proceeding.

Only after planning questions are resolved should the corresponding implementation agent begin coding on that branch.

## Branch assignment

### Implementation Agent 1

Work only on:

- feat/ledger-and-earnings

Read and follow:

- /codex-agent-plan/PROMPTS/phase-6-ledger-and-earnings.md

### Implementation Agent 2

Work only on:

- feat/platform-guardrails

Read and follow:

- /codex-agent-plan/PROMPTS/phase-6-platform-guardrails.md

## Orchestrator responsibilities

- first spawn one planning agent per branch
- ensure each planning agent stays in plan mode only
- collect a short implementation plan from each planning agent before any coding begins
- forward any unresolved questions to me before moving to implementation
- launch one implementation agent per branch after planning is resolved
- ensure each implementation agent stays strictly within its assigned scope
- identify likely overlap points early
- collect separate report-backs from both implementation agents
- summarise likely merge touchpoints
- recommend merge order when both branches are finished

## Likely overlap points to watch

These branches may overlap on:

- invoke/payment outcome handling
- shared idempotency or replay semantics
- provider-facing finance route wiring
- shared error handling and logging

## Coordination rules

- each implementation agent must stay strictly within its assigned branch scope
- do not let one agent implement the other branch’s logic
- do not allow broad refactors
- prefer isolated services and reusable hooks over invasive rewrites
- build on current invoke and x402 structures
- add tests as part of each branch, not afterwards

## Merge recommendation

When both branches are complete, recommend this merge order:

1. feat/ledger-and-earnings
2. feat/platform-guardrails

## Report back

Collect separate report-backs from each implementation agent with:

- summary of changes
- files changed
- tests run
- likely merge touchpoints
- blockers or follow-up risks

Then provide one orchestration summary that includes:

- whether the branches stayed in scope
- likely merge conflicts
- recommended merge order
- any docs that should be updated before the next phase
