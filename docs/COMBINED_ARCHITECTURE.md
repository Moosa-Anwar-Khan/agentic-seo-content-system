# Combined architecture and MVP decisions — version 1.2.1

The implementation has one authoritative LangGraph workflow, one run state, one local SQLite workspace and one review portal. It does not call a second application to execute another competing workflow.

## Components retained from each project

| Source project | Strong component | Combined implementation |
| --- | --- | --- |
| agentic-seo | Durable seven-agent workflow, checkpoints, queued jobs, targeted revisions | `src/orchestrator/`, `src/services/jobs.py`, recovery services |
| agentic-seo | GSC/WordPress analysis, evidence-based recommendations and selection | integrations, decision/research services and existing portal |
| agentic-seo | Session authentication, MFA, CSRF, reviewer accounts, safe local exports | security, API, frontend and exporter |
| agentic-seo | Local HTML, verified company assets, separate social/image asset reviews | renderer assets, social service, optional image tools |
| Earlier prototype | Public-IP checks, DNS pinning, controlled redirects, robots and bounded text extraction | adapted `src/combined/fetch.py`, `extract.py`, contracts/enums |
| Earlier prototype | Explicit Markdown section markers and structured editorial rules | adapted `src/combined/markdown.py`, versioned rule packs |
| Earlier prototype | Research before drafting, primary-source evidence and module-level editorial discipline | combined research/evidence/models/editorial modules integrated into the existing agents |
| New integration | Claim/source sheet, source freshness, revision-bound manual infographic, release confirmations | `src/combined/`, new API/portal controls and acceptance tests |
| New integration | Shared monetary reservations for ordinary text and SEO runs | combined cost scope, existing atomic storage ledger, model/SEO adapters |

The earlier prototype's separate orchestrator, user interface, storage layer and secrets are not copied. Original `seo_os` imports in the selected modules are mapped to `src.combined`. The original guarded-fetch tests and their fake web are retained with these adjusted imports.

## Requirement mapping

| Requirement | Result |
| --- | --- |
| Existing WordPress, not a replacement website | Public read-only content inventory; standalone HTML export |
| ICP 1, including one property with growth intention | Default ICP JSON and description updated; numeric core segment remains two properties |
| GSC + DataForSEO + existing content | Measured inputs retained with reporting windows and coverage limitations |
| 6–8 pillars, children and internal links | Typed topic map, graph/HTML/CSV export and human topic selection |
| Strong research for later agents | Competitor body summaries, questions, source provenance, retrieval dates, saved texts and bounded handoffs |
| Seven agents, saved outputs and targeted revisions | Original durable graph; each stage receives typed state and explicit dependencies |
| Blog and landing guidelines | Full uploaded English guidelines supplied to drafting; separate module matrices and deterministic checks |
| Primary sources for tax statements | Configured official-host allowlist, full text retrieval/cache, literal citations, tax year, semantic audit and human check |
| Website, LinkedIn, Google Business Profile | Website export plus two independent social drafts and approval records |
| Human approves and uploads manually | Only Alex/Sam can approve; no publishing integration; export status identifies pending/approved content |
| Efficient and affordable MVP | SQLite, one workflow, bounded research, independently configured model routes, output/token limits, cached sources and monetary reservations |

## Source and brand decisions

The two English brand guidelines in `docs/` (fictional sample versions in this public showcase) govern reader-facing modules. `LANGUAGE_POLICY.md` governs the English-development/German-output boundary.

Blog and landing-page colors remain separate. A placeholder logo, open-licence fonts and placeholder photography are bundled locally. The blog table of contents follows S03. The machine packs retain the original approved facts and price rows; the unsupported “100% digital” fact was removed. Missing real testimonials or prices are not filled by the model. Platform scope is website, LinkedIn and Google Business Profile; a permissible Instagram link in the brand's landing-page footer does not create an Instagram publishing workflow.

The source catalog starts with six relevant EStG provisions and a keyword-matched BMF purchase-price-allocation pack. Tavily discovers additional allowlisted official candidates; direct retrieval supplies original texts, with optional eligible Firecrawl extraction. These are controlled inputs, not a comprehensive legal research database. A tax professional must curate it for each subject and resolve applicability questions. Competitor pages and examples guide coverage or style only. Literal quote matching is supplemented by a model audit and required human review; it does not prove that every statement is correct.

## Approval lifecycle

`research → topic map → human selection → briefing → draft → brand check → SEO/factual check → human review → local export`

The required infographic is prepared manually from the NotebookLM brief, checked and attached to the current revision. The release gate blocks missing/stale evidence, unresolved questions, invalid module/link rules and absent graphics. An authenticated tax-adviser account records professional review of the exact draft, sources and graphic before Alex/Sam can give final approval. The administrator verifies professional qualifications. Review is bound to identity, revision and evidence/graphic fingerprint. Final review also records factual, graphic and manual-upload confirmations. Website approval does not approve either social post. Prepare and approve social adaptations before publishing. Each uses the existing enquiry CTA by default. A person can later record the actual WordPress URL; changing a social post to that article link resets its approval. No URL reachability check or upload is implied by this record. A revision invalidates old social assets and requires a fresh graphic attachment.

The `publisher_agent` name is retained for compatibility, but it only writes local files. It has no WordPress mutation client. Neither LinkedIn nor Google Business Profile has an automated upload adapter.

## Provenance and packaging


## Version 1.2.1 operational controls

Atomic per-run and shared UTC-month reservations cover text, SEO, Tavily, Firecrawl and optionally enabled paid image calls in combined mode. Provider allowances must be checked against the account contract; unknown credit-based charges remain reserved. The ledger cannot guarantee vendor invoices or count external spending.

Tavily and Firecrawl are direct API adapters, not a generic MCP server runtime. Responses use encrypted operation storage and uncertain paid calls pause for inspection. Optional Langfuse remains deferred.

The portal supports the professional-review role, monthly-budget view, manual publication URL and dated manually counted qualified enquiries. HTML export is primary; Google Docs is a manual import fallback. Neither Docs nor NotebookLM is an automated connector.

## Role implementation and preview scope

Seven core roles comprise five AI-powered steps, one deterministic Brand Check and one code exporter. Social is an additional AI task; research/assessment can add model calls. The configured legacy brand route does not make combined Brand Check an AI step. Combined page previews and the dashboard still require a dedicated visual/usability pass; full design compliance is not claimed. See EDITORIAL_BRANDING.md.
