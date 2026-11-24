---
name: muster-partner-blog
description: Sample brand and structure guideline for blog articles of the fictional tax-advisory firm "Muster & Partner". Loaded at runtime and passed to the writer and reviewer models.
version: 1.0-sample
---

# Brand Guideline Blog Articles – Muster & Partner (fictional sample)

> This is a fictional sample written for the public showcase. It shows the shape of guideline the system expects: firm facts, tone, a fixed module catalogue (S01–S19), design tokens and SEO rules. Replace it with the real client firm's approved guideline.

## 0. Blog article or landing page?

A **blog article** explains a tax question or situation; its goal is to inform, build trust and rank. A **landing page** describes the firm's own offering and asks for an enquiry. Everything that is not a blog article uses the landing-page guideline.

## 1. Firm facts

Use only the approved facts in `inputs/brand-guidelines/facts.json`, word for word. Never invent numbers, client counts, ratings, guarantees, case studies or comparisons with competitors.

## 2. Tone of voice

- Address readers formally with **"Sie"**; the firm speaks as **"we"**.
- Competent but plain: short sentences, one idea per paragraph, explain technical terms on first use.
- One practice perspective in the introduction is allowed ("In unserer Beratungspraxis …").
- No fear-mongering, no exclamation-mark marketing, no promises of tax savings.

## 3. Article structure – module catalogue

P = mandatory, O = optional.

- **S01 · Breadcrumb & category — P**
- **S02 · H1 + lead — P.** H1 pattern "[main keyword] — [situation/benefit]"; lead of two or three short sentences.
- **S03 · Meta line — P.** Author, review date, reading time.
- **S04 · Table of contents — P**
- **S05 · Introduction — P.** The reader's situation and what the article answers.
- **S06 · Situation navigator — P.** Three or four situations with jump links to the matching section.
- **S06a · Main infographic — P.** One infographic summarising the H1 topic.
- **S07 · Classification — P.** "What is …?" / "When does … apply?"
- **S08 · Core sections (3–7 H2) — P.** Each with official sources and at least one contextual guide link.
- **S09 · Target-group section — O**
- **S10 · Long-tail questions as H2 — P (at least 2).** Phrased as real search questions.
- **S11 · Decision aid "Can I do this myself?" — O**
- **S12 · Priority table "When should I act?" — O**
- **S13 · Documents checklist — P** for topics that involve handing documents to an adviser.
- **S14 · Immediate actions "What you can do today" — P**
- **S15 · Related articles — P.** Existing related reading only; never invented URLs.
- **S16 · Conclusion — P**
- **S17 · Next step + final CTA — P.** Calm, links to the enquiry page.
- **S18 · Social media bar — P**
- **S19 · Template areas — not filled by the author.**

## 4. Callouts and elements

Callouts: "Wichtig" (light blue), "Gut zu wissen" (green), "Achtung" (red). Tables need a header row and a caption. Checklists use real check items, not prose.

## 5. CTAs and links

Every CTA leads to the enquiry page. Blog CTAs stay calm: at S14 and S17 only. Social links use the maintained channel URLs from configuration.

## 6. Design Tokens (Blog)

| Token | Value | Usage |
|---|---|---|
| Primary blue | `#1F5C99` | Links, buttons, accents |
| Dark blue | `#1B2F4A` | CTA block, table header |
| Headings | `#1B1A18` | H1–H3 |
| Body text | `#55524d` | Paragraphs |
| Secondary text | `#98958F` | Meta line, captions |
| Borders | `#DAD6CF` | Cards, tables |
| Warm background | `#F2F0EA` | Situation navigator, immediate actions |
| Light blue | `#E7EEF8` | Callout "Wichtig" |
| Green | `#059669` / `#EBFBF3` | Callout "Gut zu wissen" |
| Red | `#A03B33` | Callout "Achtung" |

Typography: headings in **Montserrat** (600), body text in **Cabin** (400), 17–18 px, line height 1.7, text width about 720 px. Alt texts are descriptive and in German.

## 7. SEO Rules

| Element | Rule |
|---|---|
| Main keyword | In H1, title, meta description, URL, first paragraph and at least 2 H2s |
| Title tag | ≤ 60 characters: `[Keyword] – [Benefit] \| Muster & Partner` |
| Meta description | 140–155 characters |
| URL | Short, lowercase, umlauts as ae/oe/ue, no date |
| Headings | Exactly one H1; do not skip levels |
| External links | Official sources only (gesetze-im-internet.de, BMF, BFH, ELSTER) |
| Schema | `Article` + `FAQPage` + `BreadcrumbList` |
| Currency | Amounts, allowances and deadlines always with the tax year |

## 8. Quality checklist before publication

Facts verbatim from the approved list · every legal statement backed by an official source · keyword placement complete · links verified · infographic present · professional review and final approval recorded. Publishing is always manual.
