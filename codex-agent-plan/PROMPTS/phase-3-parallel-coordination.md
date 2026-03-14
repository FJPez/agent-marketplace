# Parallel Coordination Handoff: Phase 3

Read these files first:

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/03-data-model-and-mutability.md
- /codex-agent-plan/docs/06-branch-and-workstream-plan.md
- /codex-agent-plan/docs/07-phase-by-phase-implementation.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md
- /codex-agent-plan/PROMPTS/phase-3-revisions-and-change-tokens.md
- /codex-agent-plan/PROMPTS/phase-3-pricing-and-publish.md
- /codex-agent-plan/PROMPTS/phase-3-discovery-api.md

You are the orchestration agent for Phase 3.

Your job is to launch and coordinate 3 separate implementation agents running in parallel on separate branches. You are not the primary implementation owner of any feature branch.

Use 3 isolated implementation agents, each working only on its assigned branch.

## Branch assignment

### Implementation Agent 1

Work only on:

- feat/revisions-and-change-tokens

Read and follow:

- /codex-agent-plan/PROMPTS/phase-3-revisions-and-change-tokens.md

### Implementation Agent 2

Work only on:

- feat/pricing-and-publish

Read and follow:

- /codex-agent-plan/PROMPTS/phase-3-pricing-and-publish.md

### Implementation Agent 3

Work only on:

- feat/discovery-api

Read and follow:

- /codex-agent-plan/PROMPTS/phase-3-discovery-api.md

## Orchestrator responsibilities

- launch one implementation agent per branch
- ensure each implementation agent reads the correct prompt and only that branch’s scope
- keep the three workstreams isolated
- identify likely overlap points before coding proceeds too far
- collect separate report-backs from all agents
- summarise likely merge touchpoints
- recommend merge order when all branches are finished

## Likely overlap points to watch

These branches may overlap on:

- service lifecycle naming
- public vs internal service field boundaries
- pricing and contract snapshot semantics
- service update paths
- route wiring if multiple branches add routes

## Coordination rules

- each implementation agent must stay strictly within its assigned branch scope
- do not let one agent implement another branch’s logic
- do not allow broad refactors
- prefer isolated changes and reusable hooks over invasive rewrites
- reuse existing provider-services structures and current identity model
- add tests as part of each branch, not afterwards

## Merge recommendation

When the branches are complete, recommend this merge order:

1. feat/revisions-and-change-tokens
2. feat/pricing-and-publish
3. feat/discovery-api

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
