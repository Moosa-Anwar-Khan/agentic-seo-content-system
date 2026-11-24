# Current workflow role contracts - version 1.2.1

Seven registered roles exchange typed saved state. Five core roles make model calls in combined live mode; Brand Check and Export are deterministic code. Models may share a provider/model. Research and assessment services can add calls without adding core agents.

The [prompt reference](AGENT_PROMPTS_REFERENCE.md) records current fixed texts and
clearly separates legacy paths. Runtime source remains authoritative.

| Role | Inputs and work | Output / gate |
| --- | --- | --- |
| SEO Strategy - AI | Research scope, approved audience profile, GSC, DataForSEO, public pages, bounded competitor evidence. Interpret measurements without fabricating data. | `SEOInterpretation` assembled with original records into `SEOAnalysis`; saved research packet. |
| Topic Map - AI | Bounded analysis, coverage and existing-page evidence. Plan 6-8 pillars, clusters and links. | `TopicMap`; code validates IDs/relationships; assessment produces recommendations; pause for human selection. |
| Briefing - AI | Selected work, official original texts, brand context and permitted feedback. | `Briefing` / `SelectedBriefing`; preserve selected topic, intent, page type and URL. Missing official text blocks live drafting. |
| Content - AI | Brief, effective brand rules, original evidence and revision notes. | `EditorialOutput`, compatibility `Article`, HTML preview and `GraphicBrief` for manual NotebookLM work. No fabricated claims, reviews, prices or URLs. |
| Brand Check - code | Structured Markdown modules and effective blog/landing rules. | `CheckResult`; check a bounded subset of module order, headings, language, metadata, length, CTA and link rules. No combined-mode brand-model call; human tone/design review remains necessary. |
| SEO QA - code + AI | Full draft, brief, retrieved evidence and supported claims. | Literal/year/source checks and semantic tax/intent audit produce `CheckResult`; passing is not professional approval. |
| Export / Publisher - code | Approved current revision, passed QA, current professional attestation and required graphic. | Local HTML/Markdown/JSON/CSV/evidence/assets only. No platform write adapter. |

## Human gates

1. The authorized owner, administrator or trusted final reviewer selects the recommendation. Link-only work stops at its report.
2. After automated QA, attach the required current-revision WebP infographic.
3. An authenticated, enabled `tax_adviser` account attests to the exact draft, official sources and graphic. Administrators verify qualifications outside the software.
4. Alex/Sam give final editorial approval with factual/graphic/manual-upload confirmations. Unresolved blockers or a stale fingerprint prevent release.
5. Approve LinkedIn and GBP posts separately. Website approval does not approve either post; live social preparation requires the approved/exported article, not prior publication.

## Auxiliary tasks and controls

Social Content is an additional AI task. Code appends the existing enquiry CTA; models cannot invent links. Changing to a manually recorded published article URL resets the affected post's approval.

Optional image prompting and image generation are disabled by default. Paid images require an explicit request allowance and share the combined per-run/monthly ledger, in addition to the attempt limit. They do not replace the required infographic. NotebookLM operation is manual and outside that API ledger.

Feedback targets briefing or content; downstream checks rerun, changed content invalidates professional/graphic checks and older social assets. Completed upstream strategy and valid paid responses are reused. Limits bound both automatic and human revisions. Source text and feedback are data, not instructions to publish or change permissions.

## Effective QA responsibilities

The active `guided-material-qa-v1` contract uses dedicated read-only page, module
and claim prompts/schemas. Page QA checks the complete current draft's intent,
coverage, metadata and cross-section consistency, retaining approved facts and
service scope. Historical company-page bodies stay with module evidence; they are
not current draft sections. An omission on an older website does not disprove an
approved company fact. Module QA checks assertions, context and link relevance;
claim QA checks substantive tax statements against original official sources.

Code owns approved forms, unique destination counts and provenance. Link-relevance
findings need literal saved body quotations; headings alone do not suffice. Material
contradictions need appropriate conflicting passages. Minor wording and presentation
are suggestions, while pricing, infographic and human inputs remain release gates.
Unsupported, malformed or out-of-scope reviews stay unresolved and cannot pass QA.
See the [active contract](../src/services/guided_qa.py) and decision guide.

Fingerprints cover prompt, schema, system, validator, context, evidence, route and
model options. Valid accepted checks are reused only after compatibility validation
with provenance; failed paid responses and historical records remain preserved.
All active QA schemas retain response-size, repetition/completion and encrypted
recovery safeguards. Explanation text has no arbitrary character cap. The current
configured QA output limit is distinct from the run's token allowance.

Preflight reports token reservation bounds and stops requests already known not to
fit. Atomic request reservations remain enforced. An unchanged invalid review is
blocked until a verified repair changes its applicable identity. Paid rechecks need
explicit authorization; refresh only reads saved information. Both portal status
panels use the same derived summary, and downloads remain unapproved until final
approval/export. Legacy schemas remain available for historical recovery, not as
competing instructions for new reviews.
