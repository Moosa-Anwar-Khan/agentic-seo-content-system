# Extending the current system - version 1.2.1

The supported MVP uses `COMBINED_MODE=true` and ICP1. Existing optional components are not permission to expand the agreed scope.

## Additional audiences and roles

ICP schemas accept JSON/YAML, but combined-mode API and workflow guards deliberately reject ICP2-6. Adding an ICP file alone does not enable it. A future expansion must deliberately update those guards, audience-specific inputs, evidence rules, portal options and meaningful tests. Do not turn combined mode off to bypass its review/source controls.

New trusted agent modules can register through `src/orchestrator/registry.py` and `inputs/config/pipeline.json`. Preserve required role order, stable briefing/content revision targets and the final review/export gates. New state fields belong in typed contracts with compatibility defaults. Adding a standard role differs from adding a new gate or human interrupt, which requires an explicit workflow contract.

## Sources and MCP

The current source catalog contains six EStG entries plus a keyword-matched BMF topic pack. Tavily discovers additional candidates. `src/combined/evidence.py` permits HTTPS official hosts for Gesetze im Internet, BMF, BFH, Recht.Bund and ELSTER; this is a host policy, not a four-URL limit. Retrieval is capped at 12 source URLs per selected topic. New subject matter needs professional curation and applicability review.

Tavily and optional Firecrawl use direct APIs. A future MCP adapter may wrap narrow tools with explicit authentication and budgets, but no generic MCP server/runtime is shipped. Transport changes must preserve current provenance, credential, recovery and no-publishing boundaries.

## PostgreSQL

SQLite is the local default. The optional adapter is `src/storage/postgres_storage.py`; checkpoint creation is in `src/storage/factory.py`. Optional dependencies are included in the lockfile. Set `STORAGE_BACKEND=postgres` and a TLS DSN with `sslmode=require` or `verify-full`. Use a direct connection or session pooler; session advisory locks make transaction pooling unsuitable here. Business/checkpoint tables use the separate `agentic_seo` schema; do not expose it through a public REST schema.

Changing configuration does not migrate existing SQLite states, audits, checkpoints or artifacts. Transfer both persistence layers deliberately and use shared artifact storage for multiple hosts. A dedicated test DSN is required for integration checks; this delivery makes no live-database validation claim.

A replacement storage backend must implement the complete `BaseStorage` contract, including run locks and atomic token/per-run/monthly currency reservations, and use a compatible durable LangGraph checkpointer. Replacing only business storage does not migrate execution checkpoints.

## Other optional work

Langfuse remains deferred. Google Docs is manual import; NotebookLM is manually operated. Automatic WordPress/LinkedIn/GBP publishing remains outside the MVP. Historical alternative-stack and audience plans in `../archive/` are references only.
