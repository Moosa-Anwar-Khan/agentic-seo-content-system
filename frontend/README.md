# Local review app

First generate API configuration and start FastAPI from the project directory
(see `../README.md`). Then run `npm ci` and `npm run dev` here. Requires
Node.js >=20.9. Default URL: http://127.0.0.1:3000.

`API_BASE_URL` configures the server-side proxy and defaults to
http://127.0.0.1:8000. Copy `.env.example` to `.env.local` to change it. A
reviewer signs in with email, password, and an authenticator code. The browser
receives a 12-hour HttpOnly session cookie; passwords and codes are not stored in
browser storage. The portal submits long-running work as durable jobs and restores
their status after a refresh.

`npm run build` creates the production build; `npm run typecheck` checks types.
`npm start` starts the local build. This app supports the review workflow;
approval creates a local export. UI labels and actionable error guidance are temporarily English; generated deliverables and their previews remain German. Status/action
codes remain English in API payloads and receive English display labels.


The shared branded shell uses local copies of the original logo and bundled fonts under `public/brand`; no external font requests are needed. Keep these byte-identical to `../src/assets/brand` originals. UI strings are centralized in `lib/ui.ts`, request handling in `lib/api.ts`, and safe error guidance in `components/ErrorNotice.tsx`. System status calls the authenticated, read-only `/system/readiness` endpoint through the same-origin proxy.

Workflow stages only change visibility and preserve the current inputs, permissions and revision-bound actions. Stage navigation does not submit a job. Existing German evidence notes can be inspected in explicitly labelled content areas; they are not silently machine-translated or removed. Unknown operational findings receive an English review instruction alongside the original German finding.

For the optional browser regression check, install Playwright in the development environment (`python -m pip install playwright`) and, if needed, its Chromium (`python -m playwright install chromium`). From the repository root run `python scripts/verify_portal.py`. It uses ports 3108/8018, clears provider credentials, blocks external application requests and creates a disposable database under ignored `var/assessment/portal-browser`. Existing Chrome is used on macOS; set `PORTAL_CHROME_EXECUTABLE` for another browser path, or use Playwright Chromium. It never starts a paid provider request or publishes anything.

Account and history strings are in `lib/accounts.ts`. `RegistrationForm`, `RunSharing` and `SessionGuard` preserve the existing FastAPI cookie/MFA authentication. Registration and bootstrap routes are public through the same-origin proxy; run sharing, owner assignment and administrator decisions require a valid session and CSRF token. No API keys are browser-visible.

For the isolated account browser checks run `python scripts/verify_accounts.py` from the repository root after building. It uses ports 3109/8019, disposable SQLite accounts, offline providers and isolated browser contexts; artifacts go to ignored `var/assessment/accounts-browser`. See `../docs/ACCOUNTS_AND_PRIVATE_HISTORY.md` for migration and recorded verification results.
