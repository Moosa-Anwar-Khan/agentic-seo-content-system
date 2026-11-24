# Current editorial branding — design contract 2.2

## Effective sources and precedence

The October English landing-page and blog guides remain the visual authorities at their existing runtime paths. Approved runtime content, linking, optional-review and release policies retain precedence over older business instructions. Original guides, the April PDF and its extracted JSON are unchanged.

The executable visual contract is `../src/assets/brand/design-contract.json`, with shared CSS in `../src/assets/brand/editorial.css`. Landing pages use the guide's #24569A primary, #1B2436 navy, Cabin 18px/1.6 body, Montserrat 600 headings, 8px buttons and 12px cards. Section spacing is 80px desktop/56px mobile, within the documented ranges. Blogs use #1F5C99 primary, #1B1A18 headings, #55524d body, Cabin 18px/1.7 and approximately 720px text width. Blog/planning spacing and unspecified common dimensions are documented implementation choices, not invented content rejection rules.

## Shared presentation and preserved work

The combined article renderer, older standalone renderer and planning reports share the same contract/fonts. Existing Markdown becomes page-specific cards, steps, FAQ panels, scrollable tables and CTA links without rewriting its wording. Blog contents form a right-hand sticky sidebar on wide screens and a keyboard-operable collapsible panel below the meta line on smaller screens. The original logo, real published company photographs and fonts are hash checked and embedded; rendering performs no provider requests.

Version 2.1 adds the landing eyebrow, price/fee card and three key-figure tiles; blog breadcrumb, lead and author/meta styling; distinct labelled callouts and italic practice quotes; captions and existing service/checklist/decision cards. Blog backgrounds target the actual article-flow structure: situation navigation and immediate actions are warm, ordinary sections white. A green callout inside the final CTA retains dark text. See the guideline audit and exceptions.

Version 2.2 centralizes component/action spacing: 24px before standalone action groups, 16px between grouped buttons, 12px between images/captions and 20px copy spacing. Existing guideline-defined section spacing and card padding remain. One shared presentation pass groups existing standalone buttons across current/legacy articles, reports, review pages and package navigation; ordinary prose links and targetable containers remain intact. Component boundaries own their gaps, and first/last-child resets prevent doubled internal margins. Adjacent FAQ rows deliberately remain one padded accordion. These are documented layout defaults, never semantic QA rules.

Current app article/review previews and newly assembled downloads render saved structured content read-only through `../src/services/presentation.py`. They do not overwrite historical state HTML, revisions, approved directories, paid results or attestation records. Infographics require matching revision, safe path, valid dimensions and SHA-256. Invalid/stale attachments remain visibly unresolved. Saved content QA and professional fingerprints do not include a CSS-only presentation change. Text/evidence changes still follow normal invalidation rules.

Theme-owned blog areas are excluded from imported content. The generic Elementor candidate contains a scoped HTML widget, with the article's approved text and presentation; it is not a reconstruction of the site's private widget/field configuration.

## Planning and package navigation

Topic plans, recommendations, package navigation and independent-review screens use an explicit neutral planning profile with original branding, Cabin/Montserrat and restrained blue/grey values. Planning documents remain labelled as plans, not completed pages. The planning profile is a presentation choice derived from existing brand assets; it does not impose customer-conversion modules on reports. German deliverables and English operator guidance retain their respective languages.

## WordPress handoff remains unverified

`../src/services/website_package.py` supplies a styled preview, content fragment, unverified Elementor-format candidate, original assets, metadata, short instructions and a hash/revision/branding manifest. Existing download endpoints remain compatible. Evaluation ZIPs additionally retain the saved infographic, isolated social drafts, three platform images when available, and their unapproved labels. Nothing uploads or publishes.

**WordPress compatibility not yet verified.** Public markup identifies Astra/Elementor, but no editable service/blog template exports or private WordPress test copy were supplied. PHP, WP-CLI and Docker are unavailable locally. The candidate's HTML-widget restrictions, inline fonts/images, SEO-plugin mapping, shared title/header/footer and media retention have not been tested on that installation. The manifest explicitly sets `upload_ready: false`; content approval does not imply import readiness. Do not ask operators to repair CSS or layouts: complete the integration and a private import test before claiming no-adjustment uploads.

Branding is displayed separately as applied, with its version and content revision. Import limitations are under expandable details and never gate standalone preview/download. Elementor files are not a prerequisite for correct standalone branding.

## Verification and remaining responsibilities

`../scripts/verify_branded_outputs.py` exercises complete synthetic desktop/mobile articles, planning, package navigation and CSS containment in Chrome without external requests. Computed component values are compared with independent guideline expectations; structural checks inspect assembled markup rather than relying solely on stylesheet equality. These are local presentation tests, not an Astra/Elementor import, an audit of every future article, or professional approval. Record current dated results in the technical report; do not substitute older screenshots.

`../scripts/verify_output_spacing.py` additionally measures element rectangles across complete templates, responsive breakpoints, long labels, unequal cards and 200% text sizing. It checks component/action separation, grouped controls, caption gaps, flow overlap, clipping and overflow. The exact saved L04 card/button arrangement is tested independently; colour/font assertions alone cannot establish layout quality.

Approved facts, applicable fees, personal statements and human suitability remain release responsibilities. Testimonials are optional; omit unavailable reviews and ratings. Automated brand/QA checks cover a useful subset, not every visual or semantic rule. Revision 6 explicitly corrects the saved graphic wording, caption and alternative text through the audited edit path. Revision 5 and the original design brief remain historical evidence; affected checks and the infographic binding must be renewed.

The original guides' secondary text colours have insufficient normal-text contrast on white. They remain exact guideline values; the audit records this conflict instead of silently changing the palette or claiming full accessibility conformance.

Visible FAQs remain useful, but runtime output does not target FAQPage or self-serving AggregateRating rich results. Original wording is preserved. Historical accounts in `../archive/EDITORIAL_BRANDING_HISTORY.md` remain history.
