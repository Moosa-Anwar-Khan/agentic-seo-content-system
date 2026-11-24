"""Complete synthetic component examples, never approved business outputs."""
from src.combined.models import Module
from src.combined.pipeline import demo_draft

CTA='[Beratung anfragen](https://steuerkanzlei.example/mandant-werden/)'


def representative_draft(state,settings,page_type):
    state.briefing=state.briefing.model_copy(update={'page_type':page_type})
    draft=demo_draft(state,settings)
    landing={
        'L01':'# Steuerberatung für Ihr Unternehmen\n\nBuchhaltung · Steuererklärungen · Digitale Zusammenarbeit\n\nSie möchten Ihre Unterlagen ordnen und Ihre Fragen im persönlichen Gespräch klären. '+CTA,
        'L02':'## Erkennen Sie sich wieder?\n\n- **Belege verteilt:** Papier und digitale Unterlagen liegen an verschiedenen Orten.\n- **Fragen offen:** Sie möchten den nächsten Schritt verstehen.\n- **Zuständigkeiten unklar:** Halten Sie Ihre bisherigen Aufgaben fest.\n- **Gespräch vorbereiten:** Sammeln Sie Ihre Fragen vorab.\n\n> Ein geordnetes Gespräch beginnt mit Ihren Fragen.',
        'L03':'## Für wen ist die Beratung relevant?\n\n| Situation | Vorbereitung |\n|---|---|\n| Einzelunternehmen | Vorhandene Unterlagen sammeln |\n| Gemischte Belege | Bisherige Ablage beschreiben |',
        'L04':'## Was Sie bei uns anfragen können\n\n### Finanzbuchhaltung\n\nBeschreiben Sie Ihre bisherige Organisation.\n\n### Steuererklärungen\n\nBenennen Sie die betreffenden Zeiträume.\n\n### Digitale Zusammenarbeit\n\nNennen Sie Ihre bisherige Software.\n\n'+CTA,
        'L04a':'## Ihr Anfrage-Kompass\n\n| Schritt | Ihre Vorbereitung |\n|---|---|\n| Fragen | Anliegen notieren |\n| Unterlagen | Vorhandene Belege ordnen |',
        'L05':'## Kosten für Ihren Leistungsumfang\n\n| Leistung | Preisangabe |\n|---|---|\n| Finanzbuchhaltung | [PREIS FEHLT] |\n| Steuererklärungen | [PREIS FEHLT] |\n\nStBVV: Eine konkrete Preisangabe ist noch freizugeben.',
        'L06':'## Was Sie vorab klären können\n\n**Unterlagen:** Notieren Sie, was bereits vorliegt.\n\n**Zusammenarbeit:** Beschreiben Sie Ihre vorhandenen Abläufe.',
        'L07':'## Von der Anfrage zum Gespräch\n\n### Anfrage stellen\n\nBeschreiben Sie Ihr Anliegen im Kontaktformular.\n\n### Fragen sammeln\n\nOrdnen Sie Ihre vorhandenen Unterlagen.\n\n### Zusammenarbeit besprechen\n\nKlären Sie den individuellen Umfang persönlich.\n\n'+CTA,
        'L08':'## Persönliche Einordnung\n\n[FREIGEGEBENES STATEMENT FEHLT]\n\nEine Zuschreibung wird in diesem Test nicht erfunden.',
        'L09':'## Ihre Anliegen im Überblick\n\n**Finanzbuchhaltung:** Beschreiben Sie Ihre Aufgaben.\n\n**Steuererklärungen:** Halten Sie Ihre offenen Fragen fest.\n\n**Digitale Zusammenarbeit:** Nennen Sie Ihre Anwendung.',
        'L10':'## Digitale Zusammenarbeit\n\nÜber 12 Jahre Erfahrung in der digitalen Steuerberatung.\n\n- **1 fester Ansprechpartner**\n- **Rückmeldung innerhalb von 48 Stunden**\n- **Wechsel zu uns in 21 Tagen, vollständig digital**\n\nWelche Software nutzen Sie?\n\n- Anwendung benennen\n- Belegablage beschreiben',
        'L11':'## Das Team kennenlernen\n\nEin Team mit Erfahrung aus vielen Branchen. Das bestehende Teamfoto wird im Seitenkopf gezeigt.',
        'L13':'## Häufige Bedenken\n\n**Muss bereits alles vorliegen?** Beschreiben Sie, welche Unterlagen vorhanden sind.\n\n**Ist der Umfang schon festgelegt?** Klären Sie Ihren individuellen Bedarf im Gespräch.',
        'L14':'## Ihre Fragen\n\n### Wie bereite ich mich vor?\n\nOrdnen Sie Ihre Fragen.\n\n### Wo stelle ich eine Anfrage?\n\nNutzen Sie unsere Kontaktseite.\n\nWeiterführende Informationen finden Sie auf der [Startseite](https://steuerkanzlei.example/).',
        'L15':'## Ihr nächster Schritt\n\nBesprechen Sie Ihre offenen Fragen vertraulich.\n\n'+CTA,
        'L16':'## Weitere Einblicke\n\nNur freigegebene Kanäle werden verlinkt.'}
    blog={
        'S01':'Startseite » Buchhaltung',
        'S02':'# Unterlagen ordnen — Ihr Gespräch vorbereiten\n\nPapierbelege im Ordner. Digitale Dokumente im Postfach. Sie möchten Ihre Fragen für ein Gespräch sammeln.',
        'S03':'Muster & Partner – Steuerberatung · 22. Oktober 2026 · ca. 5 Min. Lesezeit',
        'S05':'## Orientierung für Ihr Gespräch\n\nDieser synthetische Layouttest zeigt, wie Sie Ihre Unterlagen und Fragen übersichtlich ordnen können. Er enthält keine individuelle steuerliche Beurteilung.\n\nDie folgenden Bereiche helfen Ihnen bei der Vorbereitung.',
        'S06':'## Welche Situation trifft auf Sie zu?\n\n- [📁 Papierbelege ordnen](#ihre-unterlagen-im-ueberblick)\n- [💻 Digitale Ablage beschreiben](#digitale-unterlagen)\n- [📝 Offene Fragen sammeln](#welche-fragen-bringen-sie-mit)',
        'S06a':'## Vorbereitung auf einen Blick\n\nFragen sammeln, Unterlagen ordnen, vorhandene Anwendungen benennen und das Gespräch vorbereiten.',
        'S07':'## Ihre Unterlagen im Überblick\n\nNotieren Sie, welche Belege vorhanden sind.\n\n> Achtung: Dieser Beispielsatz ist ein gekennzeichneter Layouttest.',
        'S08':'## Digitale Unterlagen\n\nBeschreiben Sie die bisherige Ablage, ohne Zugangsdaten weiterzugeben.\n\n### Was können Sie vorbereiten?\n\nHalten Sie fest, welche Anwendung Sie nutzen.\n\n> Geordnete Fragen schaffen einen klaren Ausgangspunkt für das Gespräch.\n\n> Wichtig: Bewahren Sie Ihre Originalunterlagen auf.\n\n> Gut zu wissen: Offene Fragen können Sie zunächst als Liste sammeln.\n\n| Unterlage | Vorbereitung |\n|---|---|\n| Rechnung | Vorhandene Ablage beschreiben |\n| Korrespondenz | Nach Datum ordnen |',
        'S09':'## Für Selbständige\n\n### Papier und digitale Ablage\n\nBeschreiben Sie beide Wege.\n\n### Vorhandene Anwendungen\n\nBenennen Sie die bisherige Software.',
        'S10':'## Welche Fragen bringen Sie mit?\n\nSchreiben Sie Ihre offenen Punkte auf. Die Liste hilft Ihnen, das Gespräch zu strukturieren.',
        'S11':'## Was können Sie selbst vorbereiten?\n\n### Selbst ordnen\n\nSammeln Sie Ihre Unterlagen.\n\n### Persönlich besprechen\n\nKlären Sie den individuellen Umfang im Gespräch.',
        'S12':'## Wann können Sie beginnen?\n\n| Situation | Handlungspriorität | Vorbereitung |\n|---|---|---|\n| Offene Frage | Sofort handeln | Ansprechpartner klären |\n| Ablage unklar | Zeitnah | Bestand beschreiben |\n| Gespräch geplant | Planbar | Fragen sammeln |',
        'S13':'## Unterlagen-Checkliste\n\n### Belege\n\n- Vorhandene Rechnungen\n- Bisherige Ablage\n\n### Fragen\n\n- Gewünschte Unterstützung\n- Verwendete Software',
        'S14':'## Was Sie heute vorbereiten können\n\n1. **Fragen sammeln.** Schreiben Sie Ihre Anliegen auf.\n2. **Unterlagen ordnen.** Beschreiben Sie den vorhandenen Bestand.\n3. **Beratung anfragen.** Klären Sie den passenden Umfang.\n\n'+CTA,
        'S15':'## Weiterführende Beiträge\n\n- [Website und Kontakt](https://steuerkanzlei.example/)\n- [Anfrage stellen](https://steuerkanzlei.example/mandant-werden/)',
        'S16':'## Fazit\n\nMit einer Liste Ihrer Fragen und vorhandenen Unterlagen bereiten Sie das Gespräch vor. Individuelle Aussagen bleiben der persönlichen Prüfung vorbehalten.',
        'S17':'## Ihr nächster Schritt\n\n> Nächster Schritt: Beschreiben Sie Ihr Anliegen vertraulich und unverbindlich.\n\n### Ihre Fragen persönlich besprechen\n\nDas Erstgespräch ist kostenfrei und unverbindlich.\n\n'+CTA,
        'S18':'## Mehr Steuerwissen\n\nFreigegebene Kanäle werden hier angezeigt.',
        'S19':'## Theme-eigener Bereich\n\nDieser Bereich darf im Inhalt nicht dupliziert werden.'}
    draft.modules=[Module(id=k,markdown=v) for k,v in (blog if page_type=='blog' else landing).items()]
    return draft
