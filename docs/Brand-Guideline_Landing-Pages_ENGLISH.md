---
name: muster-partner-landing
description: Sample brand and conversion guideline for landing pages of the fictional tax-advisory firm "Muster & Partner". Loaded at runtime and passed to the writer and reviewer models.
version: 1.0-sample
---

# Brand Guideline Landing Pages – Muster & Partner (fictional sample)

> This is a fictional sample written for the public showcase. It shows the shape of guideline the system expects: page types, a section kit (L00–L16), a page-type matrix, prices, design tokens and SEO rules. Replace it with the real client firm's approved guideline.

## 0. Page types

Main types: **service**, **industry/niche**, **tool** (accounting software the firm works with). Additional types as needed: **legal form**, **price**, **location**. The page type decides which sections are used (see 5.2).

## 1. Firm facts and trust signals

Use only the approved facts in `inputs/brand-guidelines/facts.json`, word for word. Ratings and testimonials appear only when genuine, approved and attributed; otherwise they are omitted.

## 2. Tone of voice

Formal **"Sie"**, the firm speaks as **"we"**. Each page follows the logic *problem → relevance → solution → proof → next step*. Concrete, calm and confident; no superlatives or guarantees.

## 3. Conversion principles

One clear goal per page: an enquiry. Show the visitor that the firm understands the situation before presenting the offer. Remove friction: short forms, clear next step, transparent prices where approved.

## 4. CTA system

- Every page has 4–6 contact CTAs, all leading to the enquiry page.
- CTA texts are written for the page; generic "Kontakt" alone is not enough.
- Secondary CTAs: phone and email from the approved facts.
- Buttons: primary blue, radius 8 px, full width on mobile.

## 5. Section kit

P = mandatory, O = optional, S = situational.

- **L00 · Notice bar — O**
- **L01 · Hero — P.** H1, benefit, primary CTA.
- **L02 · Problem hook — P.** Three or four typical problems of the audience.
- **L03 · Relevance — S.** Who the page is for.
- **L04 · Scope of services — P**
- **L04a · Main infographic — P (always)**
- **L04b · Tool collaboration — P on tool pages only**
- **L05 · Prices — P** (except location pages); only approved prices, otherwise a placeholder that blocks release.
- **L06 · Context tiles — O**
- **L07 · Process — P.** From enquiry to ongoing support.
- **L08 · Statement by the tax adviser — P.** Only an approved personal statement.
- **L09 · Service promise — P**
- **L10 · Digital & modern — P**
- **L11 · Team & experience — S**
- **L12 · Client testimonials — optional.** Genuine approved reviews only.
- **L13 · Objections — P**
- **L14 · FAQ + guide links — P.** 2–4 links to existing guide articles.
- **L15 · Final CTA — P**
- **L16 · Social media bar — P**

### 5.2 Matrix

| Section | Service | Industry | Tool | Legal form | Price | Location |
|---|---|---|---|---|---|---|
| L01–L02, L04a, L07, L13–L16 | P | P | P | P | P | P |
| L04b Tool collaboration | – | – | P | – | – | – |
| L05 Prices | P | P | P | P | P, detailed | O |
| L11 Team & experience | O | P | O | O | O | P |

L01 comes first and L02 directly after; L14 → L15 → L16 close the page.

## 6. Signature element

Each page should have one element that makes it memorable for its audience, for example a calculator teaser, a deadline timeline or a comparison table.

## 7. Prices

Prices come only from `inputs/brand-guidelines/prices.json`. Missing prices are never estimated; fee-regulated services carry the StBVV note.

## 8. Design Tokens (Landing Pages)

| Token | Value | Usage |
|---|---|---|
| Primary blue | `#24569A` | Buttons, highlights, icons |
| Primary dark | `#1C3B66` | Hover, dark sections |
| Navy | `#1B2436` | Headings, final CTA background |
| Body text | `#4A5568` | Paragraphs |
| Secondary text | `#8893A5` | Labels, footnotes |
| Light blue | `#E9F0FB` | Section backgrounds, badges |
| Borders | `#D3D7DF` | Cards, tables |
| Light gray | `#F4F5F7` | Alternating backgrounds |
| Red | `#C0392B` | Objection markers |
| Green | `#059669` | Check marks |

Typography: headings in **Montserrat** 600, body text in **Cabin** 400, line height 1.6. Section spacing 80 px desktop, 56 px mobile; cards with 12 px radius.

## 9. SEO Rules

| Element | Rule |
|---|---|
| Main keyword | In H1, title, meta description, slug and at least 2 H2s |
| Title tag | ≤ 60 characters |
| Meta description | 140–155 characters |
| Internal links | Only existing pages from the site inventory |
| Schema | `Service` + `FAQPage` + `BreadcrumbList` |

## 10. Quality checklist before publication

Approved facts verbatim · approved prices only · no invented reviews · navigator anchors resolve · 4–6 CTAs · keyword placement complete · professional review and final approval recorded. Publishing is always manual.
