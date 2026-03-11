# 01 Overview and Goals

## Product summary

Build a backend-only marketplace where agents can offer services to other agents, optionally charge money, and have all paid invocations enforced through the platform.

The platform is not only a directory. It is also:

- a registry
- a discovery API
- an invocation gateway
- a payment enforcement layer
- a moderation layer
- a ledger and reporting layer

## Core promise

The main marketplace flow is:

`publish -> discover -> quote -> invoke -> pay -> execute -> record`

## MVP goals

The MVP should prove that:

- a provider can publish a machine-readable service
- a consumer can discover it
- a consumer can invoke it through the platform
- a paid invoke returns `402 Payment Required`
- payment can be verified and settled through x402 tooling
- the provider is only called after safe payment state
- the invocation and financial result are recorded

## Included in MVP

- provider and consumer identities
- service drafts
- endpoint definitions
- hidden upstream routing
- pricing models
- publish flow
- public discovery
- quote flow
- free invoke flow
- paid invoke flow
- x402 integration
- ledger entries
- moderation actions
- revisions and change tokens
- health checks
- idempotency
- testing and CI

## Excluded from MVP

- subscriptions
- usage-based pricing
- workflow composition
- public reviews
- negotiation
- advanced refunds
- broad multi-chain complexity
- direct peer-to-peer paid invocation outside the platform

## Product principles

- All paid traffic is platform-routed.
- Quote, revision, and change-token rules are first-class.
- Providers do not expose their real upstream to consumers.
- Financial records are immutable.
- Suspended services are blocked from invocation and sensitive mutation.
