# Accounts and private saved history

Implemented for the local FastAPI/LangGraph/SQLite + Next.js portal. German deliverables, revision-bound approvals, professional attestation, final Alex/Sam approval, budget reservations and manual publication remain in place.

## Account operation

1. Start the backend with the existing `AUTH_ENCRYPTION_KEY`. If there is no administrator, the terminal prints a cryptographically random setup code. In the portal choose **Set up administrator**, enter that code and account details, then enroll an authenticator and save recovery codes.
If an operator registered as a pending member before creating the first administrator, setup can claim that same pending registration using its original password and the valid terminal code. The ID and password hash are retained, old sessions/challenges are revoked, the claim is audited and MFA enrollment remains required. Active or rejected accounts cannot be claimed. Wrong pending-account passwords count against the persistent setup attempt limit. First-admin claims still use the same atomic database lock.

2. The setup code expires in 15 minutes and changes on restart. Only its SHA-256 digest and expiry are retained in server memory. Never put the code in a URL, `.env`, issue or log service. Invalid attempts are limited to five per persistent 15-minute window, including across restarts. Setup is unavailable after an administrator exists, even if that account is disabled or has not finished MFA. Use trusted terminal recovery in that situation.
3. **Create account** registers an ordinary pending member, disabled until an administrator approves it. Passwords must match and contain 12–128 characters. Names are normalized; Alex/Sam variants are reserved. Registration is limited by account email and local proxy origin (five attempts per 15 minutes); forwarding headers cannot bypass the local proxy limit.
4. In **Account & registration management**, administrators load accounts and enter their password/current authenticator code to activate or reject a pending registration. Rejection cannot be undone by the generic enable action. Approved members sign in using their chosen password and complete MFA setup.
5. Professional/final reviewer creation remains an administrator action. Administrators must check qualifications before granting `tax_adviser`. Ordinary ownership or administrator status does not confer professional attestation or final approval.

The terminal commands remain available:

```bash
python scripts/bootstrap_admin.py --email YOUR_ADMIN_EMAIL
python scripts/bootstrap_admin.py --email YOUR_ADMIN_EMAIL --recover
```

The first creates Sam only when no administrator exists. Recovery resets an existing administrator's credentials/MFA and revokes their sessions, while retaining history and the encryption key. Email delivery, email verification and self-service password recovery are outside this phase.

## Authorization and sharing

New authenticated runs have server-assigned `owner_user_id` before any processing. Clients cannot supply or change ownership in start/job requests. Checkpoints, retries and revisions retain ownership. Linked research inherits the parent's owner and starts without sharing grants. Only the owner or an administrator can start linked research; queued research must retain access to both its child and parent.

The portal lists **My runs** and **Shared with me**, with status, revision, update time and saved-result links. Administrators also see **All runs**. Owners and administrators can grant/revoke explicit access to activated tax advisers or trusted Alex/Sam final reviewers. Sharing grants no role and does not remove approval gates. Shared professional reviewers can attest and request corrections; topic selection, quality reruns and infographic upload remain with the owner, administrator or trusted final reviewer. Disabled or pending accounts cannot use sharing to gain access.

All nested run and job routes enforce access, including previews, source/release records, AI-call/cost history, social/image assets, reports and downloads. Unauthorized direct IDs return a generic 404. Lists and idempotent replays are filtered. A child's inherited cost attribution is withheld if the viewer lacks access to the originating run; the original ledger and source provenance remain saved.

Queued work rechecks activation/access before execution, between workflow steps and at tracked paid-request boundaries. Revoking access prevents further requests; already submitted requests can complete and their results remain saved. Retrying a revoked actor's job requires restoring that actor's activation/access. There is no automatic paid retry. Logout revokes only the session and never cancels queued work or deletes results. Run/month budgets and shared application API credentials are unchanged. Monthly usage is an aggregate with no private run IDs.

The browser stores only account-scoped last-run/pending-request identifiers. It clears private views on logout, session expiry and account changes, including other tabs. Connection failures retain the last authorized loaded view with a notice; reconnection does not regenerate content.

## Additive migrations and existing data

Account-store initialization runs compatible additive SQL on startup:

| SQLite table/field | Optional PostgreSQL equivalent | Purpose |
| --- | --- | --- |
| `users.registration_status` | `seo_users.registration_status` | Existing accounts default to `active`; their existing `enabled` value is retained. New registrations are `pending`/disabled. |
| `run_owners(run_id, user_id)` | `seo_run_owners` | Explicit ownership overrides for historical assignments/transfers, authoritative over old checkpoint JSON. |
| `run_shares(run_id, user_id, granted_by)` | `seo_run_shares` | Durable explicit reviewer access, unique per run/account. |
| `access_audit(id, actor_id, action, target_id, created_at)` | `seo_access_audit` | Registration decisions, ownership assignments and sharing changes. |
| Optional `RunState.owner_user_id` JSON field | Same JSON field | New-run ownership survives checkpoints/revisions/retries; missing old fields default to null. |

SQLite uses `BEGIN IMMEDIATE` for atomic account/first-admin creation. PostgreSQL uses a transaction advisory lock and `ADD COLUMN IF NOT EXISTS`; tables remain in the existing private `agentic_seo` schema. No account, checkpoint, approval, job, operation, cost, export or encryption key is reset. Existing legacy token sessions continue to be invalidated as before this change.

Before upgrading an existing installation, stop the local backend and back up its database/checkpoint files, outputs and private encryption configuration. Keep backups private. Never delete the database or rotate `AUTH_ENCRYPTION_KEY` to apply these migrations. Run the backend once to apply the additive changes. Historical ownerless runs remain administrator-only: open **All runs**, select the run, expand **Private history & sharing**, select an activated owner and confirm with administrator password/MFA. Assignment removes existing shares, then owners may grant new ones. Names in old reviews never imply ownership. Local files already downloaded cannot be revoked.

SQLite with one backend process remains the supported local default. The optional PostgreSQL SQL is implemented but needs execution against a dedicated test PostgreSQL database before use; no PostgreSQL test connection was configured in this assessment.

## Verification

All checks used disposable databases/accounts and fake providers. No paid generation or publication occurred. 

- Complete offline backend suite: **299 passed, 2 skipped**, with four pre-existing BeautifulSoup warnings. The two skips require `TEST_POSTGRES_DSN`; PostgreSQL was not live-validated.
- New account/privacy tests: **16 passed**. Coverage includes registration/activation/rejection, password/MFA/session flows, setup expiry/throttle/reuse/restart and concurrent first-admin claims, reserved-name/role injection, owner-aware lists/direct routes/jobs/assets/costs/exports, sharing/revocation, unchanged final-approval policy, legacy assignment, additive migration, persisted history and duplicate-request handling.
- Frontend TypeScript check and production build: passed.
- `scripts/verify_accounts.py`: **8 browser checks passed**, including real proxy/bootstrap/activation/MFA flow, private saved German draft/download, disabled final approval for members, sharing/revocation, logout/re-login, cross-tab logout and mobile keyboard/no-overflow checks.
- `scripts/verify_portal.py`: **12 browser checks passed**, preserving explicit image opt-in, duplicate-click protection, variants, approvals/downloads, run switching, paid retry cancellation, connection recovery, session-expiry guidance, MFA and mobile navigation. Both browser suites reported no runtime errors.
- Desktop/mobile screenshots were inspected. Artifacts: ignored `var/assessment/accounts-browser/` and `var/assessment/portal-browser/`, with `results.json` in each directory.

Initial verification exposed ownerless old test fixtures, a sharing selector accessible-label issue, an authenticator-key loading race and a run-switch timing issue. Fixtures now explicitly assign ownership; accessible labels, loading controls and browser waits were corrected. Final results above are from reruns after those corrections.

Live provider access, actual billing, public hosting security, PostgreSQL/Docker execution and professional suitability remain separate validation work. Passing offline tests does not establish production or legal readiness.

## First-admin conflict correction

A `409 Conflict` can occur when the first-admin email was already used for ordinary pending registration. The browser setup now supports securely claiming that pending registration with its original password plus the local setup code. The UI provides specific English guidance for incorrect existing passwords, unavailable account identities and reserved names. Existing accounts are never silently promoted by startup or ordinary registration. The change was verified with disposable accounts, including concurrent pending/new claims and unchanged MFA requirements; no real account was modified by the fix.

Run `python scripts/verify_accounts.py --pending-administrator` after building to exercise the register-first scenario through the real local proxy and browser with fake providers.

Correction verification: complete backend suite **307 passed, 2 PostgreSQL skips, 4 existing BeautifulSoup warnings**. Frontend typecheck/build passed. The pending-administrator browser variant passed **9 checks**, including same-email reuse without duplicate accounts, MFA enrollment, saved private history, sharing and mobile behavior. Real account records were inspected read-only; `.env` and its private backup were not modified. No paid calls or publication occurred.
