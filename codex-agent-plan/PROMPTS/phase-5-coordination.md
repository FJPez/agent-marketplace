# Coordination Handoff: Phase 5

Read these files first:

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/05-x402-integration.md
- /codex-agent-plan/docs/06-branch-and-workstream-plan.md
- /codex-agent-plan/docs/07-phase-by-phase-implementation.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md
- /codex-agent-plan/PROMPTS/phase-5-invoke-core.md
- /codex-agent-plan/PROMPTS/phase-5-x402-payment.md

You are the orchestration agent for Phase 5.

Your job is to coordinate the execution phase across the two implementation branches. This phase is not fully parallel in the same way as earlier phases. `feat/invoke-core` is the anchor branch and should land before `feat/x402-payment` is fully merged.

## Branch assignment

### Implementation Agent 1

Work only on:

- feat/invoke-core

Read and follow:

- /codex-agent-plan/PROMPTS/phase-5-invoke-core.md

### Implementation Agent 2

Work only on:

- feat/x402-payment

Read and follow:

- /codex-agent-plan/PROMPTS/phase-5-x402-payment.md

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

## Orchestrator responsibilities

- ensure Agent 1 establishes the core invoke path first
- allow Agent 2 to prepare x402 integration pieces in parallel only where safe
- prevent Agent 2 from re-implementing invoke-core concerns
- identify shared touchpoints early
- recommend merge order and readiness
- collect separate report-backs from both agents

## Likely overlap points to watch

These branches may overlap on:

- invoke route wiring
- invocation service orchestration
- idempotency handling
- quote/request validation order
- shared error handling and logging
- provider forwarding flow

## Coordination rules

- Agent 1 owns invoke-core
- Agent 2 owns x402 payment handling
- do not let Agent 2 duplicate or fork invoke-core logic
- keep x402 logic isolated to payment/integration layers
- avoid broad refactors during this phase
- tests must land with each branch

## Merge recommendation

Recommended merge order:

1. feat/invoke-core
2. feat/x402-payment

## Report back

Collect separate report-backs from each implementation agent with:

- summary of changes
- files changed
- tests run
- likely merge touchpoints
- blockers or follow-up risks

Then provide one orchestration summary that includes:

- whether the branches stayed in scope
- whether x402-payment cleanly builds on invoke-core
- likely merge conflicts
- recommended merge order
- any docs that should be updated before the next phase
