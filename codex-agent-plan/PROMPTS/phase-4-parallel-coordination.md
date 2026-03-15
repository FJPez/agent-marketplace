# Parallel Coordination Handoff: Phase 4

Read these files first:

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/03-data-model-and-mutability.md
- /codex-agent-plan/docs/05-x402-integration.md
- /codex-agent-plan/docs/06-branch-and-workstream-plan.md
- /codex-agent-plan/docs/07-phase-by-phase-implementation.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md
- /codex-agent-plan/PROMPTS/phase-4-quote-flow.md
- /codex-agent-plan/PROMPTS/phase-4-service-health-completion.md
- /codex-agent-plan/PROMPTS/phase-4-moderation-admin-completion.md

You are the orchestration agent for Phase 4.

Your job is to launch and coordinate 3 separate implementation agents running in parallel on separate branches. You are not the primary implementation owner of any feature branch.

Use 3 isolated implementation agents, each working only on its assigned branch.

## Branch assignment

### Implementation Agent 1

Work only on:

- feat/quote-flow

Read and follow:

- /codex-agent-plan/PROMPTS/phase-4-quote-flow.md

### Implementation Agent 2

Work only on:

- feat/service-health

Read and follow:

- /codex-agent-plan/PROMPTS/phase-4-service-health-completion.md

### Implementation Agent 3

Work only on:

- feat/moderation-admin

Read and follow:

- /codex-agent-plan/PROMPTS/phase-4-moderation-admin-completion.md

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

- service status and moderation state semantics
- service or endpoint lookup paths
- shared request validation patterns
- route wiring if more than one branch adds routes
- shared logging and error handling patterns

## Coordination rules

- each implementation agent must stay strictly within its assigned branch scope
- do not let one agent implement another branch’s logic
- do not allow broad refactors
- prefer isolated services and reusable hooks over invasive rewrites
- reuse existing revisions, pricing, discovery, and provider-service structures
- add tests as part of each branch, not afterwards

## Merge recommendation

When the branches are complete, recommend this merge order:

1. feat/quote-flow
2. feat/service-health
3. feat/moderation-admin

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
