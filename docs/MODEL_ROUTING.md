# Current model routing - version 1.2.1

The application uses LiteLLM through `ModelRouter`; the LangGraph workflow controls stages. Current combined-mode model tasks include SEO Strategy, Topic Map, Briefing, Content, SEO QA and additional Social Content. Competitor summaries and existing-page assessments can make further calls using shared routes. Brand Check and Export are code, not model calls. The configured `brand` route remains available for legacy compatibility but is not used by combined Brand Check.

The selected profile in `.env.example` routes briefing and content to `openai/gpt-6.1-sol`, with low reasoning effort and standard processing. Research/SEO Strategy, Topic Map, bounded SEO QA and Social Content use `deepseek/deepseek-flash`. The profile has no automatic fallback. The public example is not proof of this installation's private configuration, live account access, model quality or available credits; inspect System status and the run's configured routes. Legacy settings still provide compatibility defaults when named routes are absent.

## Configuration

Named routes accept uppercase `TASK_PROVIDER`, `TASK_MODEL`, `TASK_FALLBACK_PROVIDER` and `TASK_FALLBACK_MODEL`. Both fallback fields are required together. Keep keys server-side in the matching provider settings. Compatible providers require an HTTPS base URL and an explicit capability declaration. Text tasks require JSON support and sufficient context; response values still pass Pydantic validation.

`AI_TASK_OPTIONS` supports validated `reasoning_effort` and `max_output_tokens`. Supported DeepSeek routes default to no reasoning when supported. Global `MAX_OUTPUT_TOKENS=12000` caps schema-specific output ceilings: `EditorialOutput` 12000, topic map/page assessment 6000, briefing 4000, `CheckResult` 2000 and social/image prompt 1500. Task overrides allow 2400 for each bounded `seo_qa`/`QAUnitResult`; the compatibility full-draft `seo_qa`/`CheckResult` ceiling is 6000, not the active combined review design. The compatibility `Article` ceiling is 8000. `MAX_RUN_TOKENS=120000` is the shipped default.

No fallback is attempted for invalid/truncated/schema-invalid output, invalid credentials or missing capabilities. Unknown timeout/network/server outcomes pause queued work for inspection rather than silently buying a replacement. A confirmed eligible rejection may use a configured fallback, consuming its own reservations.

## Evidence and bounded review

Writers receive complete eligible official-source text and original branding in ordered sections. Input/privacy/context and spending limits still apply; oversized input is blocked rather than silently shortened. Community forums are not legal authorities. Partial, hash-invalid or explicitly incompatible-year sources are excluded without rewriting their original records. Only the exact approved public company email on the configured website domain receives the narrow contact-data exception; other personal identifiers remain blocked. Offline readiness tests both complete brand prompt packages.

Combined semantic QA reviews page intent/coverage, individual modules and individual claims. A persistent revision/evidence/model/options/policy-bound manifest covers all required units; modules also check for assertions absent from the claim sheet. Completed valid units are cached immediately. Truncated, repetitive, malformed or ungrounded output never passes; simply increasing output limits is not a recovery strategy.

Saved-input preflight runs before paid briefing/writing. It reports linking inventory, missing research, configuration and remaining allowance without calling a provider. Exact requests must still fit privacy, context, token and atomic budget checks. Informational blog runs may use the creation-time starter-library quantity override; existing/omitted policies remain strict. See [workflow](WORKFLOW.md).

## Costs and records

Combined text calls require known rates verified within 30 days. Per-run currency and shared UTC-month reservations apply alongside token limits. Tavily/Firecrawl and optionally enabled images use checked request allowances; unknown USD charges remain reserved. The ledger is not an invoice guarantee.

Authenticated `/runs/{id}/ai-calls` exposes safe task/provider/model, token/cost/latency/status metadata. Final responses are encrypted in private operation storage; raw prompts, provider bodies, secrets and reasoning traces are not exposed in call history. See [paid-call recovery](PAID_CALL_RECOVERY.md) and [failure diagnostics](FAILURE_DIAGNOSTICS.md).

## Optional images

Image prompting and generation are disabled by default in application settings. The portal defaults to **No AI images**, even when generation is configured locally; choosing **Generate AI images** only reveals controls. Only an explicit generation click queues paid work. The optional image service can request one private square master and derive Website WebP, LinkedIn WebP and GBP JPG/PNG variants locally. Every variant needs separate approval. They do not replace the required manually created infographic.

Enabling paid generation requires `IMAGE_GENERATION_ENABLED=true`, a supported image route/key and a contract-checked `IMAGE_REQUEST_MAX_USD`. Combined calls use the same per-run/monthly ledger plus `MAX_IMAGE_ATTEMPTS_PER_RUN`; uncertain costs stay reserved. Local processing retries do not buy a new master. Current image-set and compatibility routes remain in `src/api/app.py`.

Providers/models must support the requested image API, resolution and capabilities. Unknown models require explicit declarations. The visual badge is not a legal-compliance guarantee. No image-provider pilot or production usability validation is claimed here.

The selected optional OpenAI image profile uses `gpt-image-2.5-flare`, medium quality and one 2048×2048 master, with local website/LinkedIn/GBP variants. A configured USD 0.25 reservation is an allowance, not an exact price or provider billing cap. Keep optional AI image prompting disabled unless separately needed; reuse the deterministic brand-aware prompt. See optional assets.
