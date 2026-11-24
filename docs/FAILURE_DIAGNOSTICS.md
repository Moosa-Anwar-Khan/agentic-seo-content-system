# Failure notices and administrator diagnostics

The English portal names the failed agent or component, operation, current revision,
confirmed reason, saved work, recovery action and retry policy. QA units and affected
image assets are identified when available. Unknown causes are explicitly unconfirmed;
content defects are separate from software crashes or provider failures.

Use **Current progress** for the present revision. **Task history** keeps older attempts
separate. Administrators can open **Technical details**, **Copy diagnostic report**, or
**Failure history → Load failure history**. Regular run viewers receive the safe summary,
not technical reports. A browser connection/rendering problem does not establish a
server-side task failure.

## Storage and compatibility

Startup adds `failure_events` (SQLite) or `seo_failure_events` (PostgreSQL). Existing
accounts, run state, checkpoints, approvals and costs are retained. Events are insert-only;
explicit retries receive a new attempt identifier. Restart recovery retains that attempt.
Existing job JSON receives optional fields with compatible defaults; old failures do not
receive invented retrospective explanations. Run history returns the newest 100 records.

Authenticated administrator-only reads:

- `GET /jobs/{job_id}/diagnostics`: saved events for every attempt of this job.
- `GET /runs/{run_id}/diagnostics`: saved job and immediate API failure events.

Both endpoints retain normal run-access checks. Existing `error_info`, job and progress
responses gain optional failure summaries without removing existing fields or codes.
Immediate authenticated run errors also receive summaries; administrators receive the
safe report inline. Requests without authorized run context are not retained as run events.

Model evidence is associated by call ID **and** job/attempt ID, never by the most recent
call in a run. Provider/model, numerical HTTP status, finish reason, token usage/limit,
validation paths and repository stack locations are included only when available.
Original request IDs are short technical identifiers; prompts, response bodies, source
text, credentials, cookies, arbitrary exception messages and external stack paths are
excluded. A failed diagnostic write logs its reference without replacing the original error.

Nothing in these controls automatically retries, generates, approves, exports or publishes.
Explicit retries can still incur charges; existing budgets and uncertain-request recovery apply.

## Verification

Tests use fake providers and disposable databases. Run:

```bash
.venv/bin/python -m pytest -q
```

Typecheck and build the frontend in an isolated copy, then point the browser script at
that build with `PORTAL_FRONTEND_DIR`:

```bash
.venv/bin/python scripts/verify_failure_diagnostics.py
```

The browser script uses ports 3110/8020, creates disposable test users/data and makes
no paid calls. Screenshots and results are written under `var/assessment/`.
Dedicated PostgreSQL integration requires an available test PostgreSQL instance.

## Verified result — 20 October 2026

- Complete offline backend suite: **417 passed, 2 skipped**, with 20 warnings.
- Includes **32 new diagnostics cases** covering boundaries, exact call correlation,
  retry attempts, persistence, access, redaction and diagnostic-storage failure.
- Browser checks: **7 diagnostics, 22 portal, 8 account checks passed**,
  including desktop/mobile layout, keyboard use, administrator history/report copying,
  access denial and preservation of saved German work after optional failures.
- Frontend typecheck, isolated production build, dependency check and diff checks passed.
- PostgreSQL instance integration was skipped; no dedicated instance was available.
- No paid provider calls, live publishing, private environment edits or server restarts.

### Load the implementation locally

The current servers were deliberately left untouched. Stop both with Control+C.
Rebuild the frontend from its directory with `npm run build`, then restart the
backend and frontend using the existing local commands. Startup adds diagnostic tables
without resetting existing records. The verified test build was isolated from `.next`
so it did not replace a build being served.
