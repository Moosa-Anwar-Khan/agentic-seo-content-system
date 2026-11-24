# Agentic SEO Content System

A multi-agent AI system that plans and writes **German SEO content for a tax-advisory firm**: blog articles, landing pages, and LinkedIn and Google Business Profile posts. Every legal statement is backed by an official source, and people approve each important step.

The system was originally built for a real firm. In this public showcase, all company data has been replaced with a fictional firm, **"Muster & Partner"**. The firm name, website (`steuerkanzlei.example`), staff names, facts, prices, brand guidelines, logo and photos are all fictional placeholders.

> **Status:** development project, not production-ready. Passing tests does not prove legal suitability, and the system never publishes anything on its own.

## What it does

| Step | What happens | Who or what does it |
| --- | --- | --- |
| 1. Research | Scans the existing website, Google Search Console and DataForSEO keyword data, and retrieves official tax sources (Gesetze im Internet, BMF, BFH, ELSTER) | AI agents and code |
| 2. Topic | Recommends topics, topic pillars and an internal-link plan; a person picks one | AI plus a human choice |
| 3. Draft | Writes a briefing, then the German article with source-backed claims and an infographic brief | AI writer |
| 4. Quality checks | Deterministic brand check (structure, links, keywords, claims) and an AI content and SEO review | Code and an AI reviewer |
| 5. Infographic | A designer uploads the graphic (WebP, 1200–1600 px wide, under 300 KB) | Person |
| 6. Professional review | A tax adviser checks facts, sources and the graphic, and signs off | Person (authenticated) |
| 7. Final approval | The final approver approves the exact revision | Person (authenticated) |
| 8. Export | Branded HTML and Markdown package with sources and review records; social drafts follow | Code |

### Engineering highlights

- **Agent orchestration with LangGraph:** typed state, saved checkpoints and numbered revisions, so interrupted runs resume where they stopped.
- **Grounded content:** official sources only come from an allow-list of government hosts. Claims must quote retrieved text, and a deterministic checker rejects ungrounded or invented findings from the AI reviewer.
- **Cost controls:** spending is reserved before every paid AI or API call, capped per run and per month. Uncertain paid requests pause instead of being repeated automatically.
- **Revision-bound approvals:** editing a draft cancels the checks and approvals that depended on the earlier version.
- **Security:** sessions with CSRF protection, authenticator (TOTP) enrolment, role-based access, encrypted secrets and server-side access checks on every run.
- **Failure learning:** recorded failures can be appended automatically to a [draft failure checklist](DRAFT_FAILURE_CHECKLIST.md) so the same mistakes are not repeated.

## Tech stack

| Area | Technology |
| --- | --- |
| Backend | Python 3.12, FastAPI, Uvicorn, Pydantic / pydantic-settings |
| Agent orchestration | LangGraph, with SQLite checkpoints and optional PostgreSQL |
| Storage | SQLite for runs, jobs, accounts, costs and failures; files in `outputs/` |
| AI models | LiteLLM and the OpenAI SDK. OpenAI writes briefings and drafts; DeepSeek handles strategy, topic planning, QA and social posts; Gemini handles AI-visibility checks. All routes are configurable in `.env` |
| Search and research data | DataForSEO, Google Search Console, WordPress public API, Tavily (discovery), Firecrawl (extraction) |
| Content processing | BeautifulSoup, Markdown, PyYAML, Pillow (images), pypdf and ReportLab (PDF) |
| Frontend (review portal) | Next.js 16, React 19, TypeScript, Recharts |
| Testing | pytest with fake providers and blocked networking; TypeScript type-check and build |
| Deployment | Runs locally; a hardened Docker Compose file is included |

## Repository layout

```text
src/
  agents/         AI roles: strategy, topic map, briefing, content, SEO QA, brand, publisher
  combined/       main pipeline: research, evidence, editorial rules, graphics, costs
  orchestrator/   LangGraph workflow and run state
  api/            FastAPI application (portal and job endpoints)
  integrations/   DataForSEO, Search Console, WordPress, official sources, website scan
  services/       QA, edits, exports, link policy, failures, images, dashboards
  schemas/        Pydantic data models
  security/       authentication, run access, input sanitising
  storage/        SQLite and PostgreSQL storage
frontend/         Next.js review portal (English interface)
inputs/           sample brand rules, facts, prices, audience profiles, official-source list
docs/             architecture, workflow, model routing, security, sample brand guidelines
scripts/          setup and verification utilities
tests/            automated tests (fake providers only)
run.py            command-line entry: demo, serve, check
```

## Quick start (offline, no API costs)

Requires **Python 3.12** and **Node.js 22 or newer**.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python run.py demo
```

The demo produces synthetic, unapproved German examples without calling any model or paid API.

### Run the review portal

```bash
cp .env.example .env
python scripts/bootstrap.py
python run.py serve
```

In a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:3000 (backend: http://127.0.0.1:8000). To create the first administrator, choose **Set up administrator** and enter the one-time code printed in the backend terminal.

For live runs, set `DEMO_MODE=false` and add your own API keys to `.env`. Never commit the filled `.env` file.

## Tests

```bash
python -m pytest -q
cd frontend && npm run typecheck && npm run build
```

Tests use fake providers and block external networking. 38 tests currently fail, all stopped by the dated-pricing safety checks: "Research price verification expired", or a preflight block on the dated model configuration. This is deliberate. The bundled model price list must be re-verified every 30 days before any paid call is allowed. Update the dated rates in `src/config/research-pricing.json` with verified provider prices to clear it.

## Documentation

- [Architecture](docs/COMBINED_ARCHITECTURE.md) and [agent contracts](docs/AGENT_CONTRACTS.md)
- [Workflow](docs/WORKFLOW.md) and [model routing](docs/MODEL_ROUTING.md)
- [Security](docs/SECURITY.md), [accounts and private history](docs/ACCOUNTS_AND_PRIVATE_HISTORY.md)
- [Paid-call recovery](docs/PAID_CALL_RECOVERY.md) and [failure diagnostics](docs/FAILURE_DIAGNOSTICS.md)
- [Editorial branding](docs/EDITORIAL_BRANDING.md) and the sample [blog](docs/Brand-Guideline_Blog-Articles_ENGLISH.md) and [landing-page](docs/Brand-Guideline_Landing-Pages_ENGLISH.md) guidelines
- [Docker](docs/DOCKER.md) and [extending the system](docs/EXTENDING.md)
