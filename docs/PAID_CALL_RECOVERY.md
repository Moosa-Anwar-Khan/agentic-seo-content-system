# Current paid-call recovery - version 1.2.1

## Durable work

Portal operations use durable queued jobs and synchronous LangGraph checkpoints. Completed stages, measured source records and validated saved replies can be reused after restart. Business state and graph execution state are separate stores. Direct synchronous compatibility endpoints do not offer every queued-job recovery guarantee.

Final model replies are encrypted with `AUTH_ENCRYPTION_KEY` before validation. Invalid/truncated replies remain failures; their safe diagnostics can be inspected and saved output can be reprocessed locally without silently buying another call. Reasoning traces are excluded. Keep the encryption key unchanged for existing state.

Unknown paid timeouts/network/server outcomes pause for explicit inspection. Restarting or repeated clicks cannot silently purchase a second request. An explicit supported job retry retains prior usage, caches completed operations and can buy new work only for the appropriate failed operation. Provider-specific research failures may require inspecting/reconciling the old request and starting an explicit fresh research run; no automatic replay is promised for an expired/failed Tavily or Firecrawl operation.

## Recover the current step

Use **Current progress** and its confirmed failure reason before reading historical attempts. A saved draft needing corrections is different from a crashed provider request. A legitimate quality failure cannot be fixed by retrying the original drafting job; inspect findings, save a free local correction or explicitly request a targeted revision. Check-only QA does not rewrite text. Local saves invalidate dependent checks, graphic binding and approvals, preserve upstream evidence/costs and consume the existing revision allowance.

Bounded QA saves completed review units immediately and reuses only matching revision/evidence/route/options/policy results. Resume unfinished work explicitly; incomplete or repetitive responses never become a pass. Do not keep increasing limits or restarting research to fix an article finding. Repeated unchanged corrections stop rather than purchasing endless repairs.

Competitor material is screened before model analysis. Unsafe pages are excluded with explicit reduced coverage; no usable evidence causes a safe stop. Partial research and original retrieval timestamps remain saved. A changed input can require a new paid analysis. Missing genuine prices, reviews, source applicability decisions and professional approvals require the team, not invented model output.

Each failed job attempt has an immutable reference; explicit retries receive new attempt IDs, while restart recovery retains the attempt. Administrators can reopen safe technical reports and failure history. The portal identifies component, operation, revision and confirmed cause without exposing raw provider text. See [failure diagnostics](FAILURE_DIAGNOSTICS.md).

## Current limits

Shipped defaults: `MAX_RUN_TOKENS=120000`, `MAX_OUTPUT_TOKENS=12000`, `MAX_RUN_USD=1.0`, `MAX_MONTHLY_USD=20.0`. Read actual configuration before running; these are application allowances, not vendor invoice guarantees. Limits do not grow themselves. Schema-specific output ceilings are in `src/services/model_routes.py`; combined `EditorialOutput` allows up to 12000 output tokens within the global cap.

Combined text, DataForSEO, Tavily, Firecrawl and enabled paid images use shared per-run/monthly currency reservations. Text rates expire after 30 days; missing/expired rates block requests. Unknown/credit-based charges remain reserved until a reliable reconciliation. Paid images also need a checked request allowance and are disabled by default. Manual tools, hosting and spending outside this database are not measured.

Source caches keep original retrieval timestamps/content hashes and can expire before approval. Refresh evidence deliberately; do not treat cache replay as a new retrieval or legal-effectiveness check. Partial provider evidence and saved failures remain visible instead of being invented or silently discarded.

## Verification and provenance

Automated tests use temporary storage, fake transports and blocked external sockets. Local tests do not demonstrate live account access, quality or billing.
