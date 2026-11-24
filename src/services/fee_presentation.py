"""Explicit run-scoped fee presentation; never invent amounts or grant release."""
import hashlib
import json


def price_pack(settings):
    raw = (settings.input_dir / 'brand-guidelines/prices.json').read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def context(state, settings):
    record = state.fee_presentation
    if record is None:
        return None  # Legacy requests retain their exact contract and cache keys.
    pack, fingerprint = price_pack(settings)
    return {
        'version': record.version, 'mode': record.mode,
        'price_pack_sha256': fingerprint,
        'approval_current': fingerprint == record.price_pack_sha256,
        'approved_stbvv_wording': pack['statement_stbvv'],
        'instructions': (
            'This explicitly approved, run-only exception overrides numeric-price layout requirements in L05 and the costs answer in L14. '
            'Use the approved StBVV wording for applicable tax-return services; bookkeeping costs are a personal enquiry. '
            'Do not invent amounts or apply the StBVV badge to services not marked stbvv in the price pack. '
            'Fee-wording-only does not approve the article or resolve other release inputs.'
            if record.mode == 'approved_wording_only' else
            'Numeric prices remain required; use only approved prices from the price pack and explicit placeholders when missing.'),
    }


def release_issues(state, settings, draft, text):
    policy = context(state, settings)
    if '[PREIS FEHLT]' in text or '[Preis folgt]' in text:
        return ['Freigegebene Preise fehlen.']
    if policy and policy['mode'] == 'numeric_prices':
        pack, _ = price_pack(settings)
        overview = next((m.markdown for m in draft.modules if m.id == 'L05'), '')
        if not any(row.get('price') and row['price'] in overview for row in pack['rows']):
            return ['Freigegebene Preise fehlen.']
    if policy and policy['mode'] == 'approved_wording_only':
        if not policy['approval_current']:
            return ['Freigegebene Gebührenregelung nach Änderung der Preistabelle erneut bestätigen.']
        by_id = {m.id: m.markdown for m in draft.modules}
        if not all(policy['approved_stbvv_wording'] in by_id.get(m, '') for m in ('L05', 'L14')):
            return ['Freigegebener StBVV-Wortlaut in Kostenübersicht und Kostenantwort fehlt.']
    return []
