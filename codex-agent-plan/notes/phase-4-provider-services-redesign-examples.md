# Phase 4 Provider-Services Redesign Examples

Examples of broader provider-services redesign work that would go beyond the
narrow Phase 4 fixes:

- Unifying `provider_endpoint_service.py` and `provider_draft_service.py` into a
  new mutation service or policy engine
- Moving publish, pricing, revision, and mutability rules into a new shared
  contract service abstraction
- Restructuring `provider_services.py` routes mainly to reduce duplication
- Redesigning repository boundaries into a new public/provider/internal query
  architecture rather than applying a narrow public-loader fix
