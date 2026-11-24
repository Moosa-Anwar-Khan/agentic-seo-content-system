from src.services.failures import diagnostic_boundary
import csv
import io
import json
from pathlib import Path
from src.orchestrator.state import RunState
from src.services.render import render_topic_map
from src.services.render import render_recommendations
from src.services.seo_decisions import verified_topic_links
from src.services.image_processing import keyword_slug
from src.services.review_contract import contract as review_contract
from src.config.settings import Settings

class LocalExporter:
    def __init__(self, root: Path, settings=None):
        self.root = root
        self.settings = settings

    @diagnostic_boundary('Local Export', 'Write local content bundle')
    def write_bundle(self, state: RunState, destination: Path | None = None) -> list[str]:
        if not state.topic_map or not state.briefing or not state.article:
            raise ValueError("Cannot export incomplete editorial artifacts")
        destination = destination or self.root / "runs" / str(state.run_id) / f"revision-{state.revision:03d}"
        # Paths are chosen by application code, never by request or model text.
        files = {
            "topic-maps/topic-map.json": state.topic_map.model_dump_json(indent=2),
            "topic-maps/topic-map.html": render_topic_map(state.topic_map, verified_topic_links(state)),
            "briefings/content-briefing.json": state.briefing.model_dump_json(indent=2),
            ('drafts-html/' + keyword_slug(state.briefing.primary_keyword or state.article.title) + '.html' if state.decision_workflow_version else "drafts-html/immobiliensteuerberater.html"): state.html,
            "reviews/state.json": state.model_dump_json(indent=2),
            "reviews/editorial-notes.json": json.dumps(state.article.editorial_notes, ensure_ascii=False, indent=2),
            "reviews/quality-checks.json": json.dumps({'run_status':state.status,
                'brand_check':state.brand_check.model_dump(mode='json') if state.brand_check else None,
                'seo_check':state.seo_check.model_dump(mode='json') if state.seo_check else None}, ensure_ascii=False, indent=2),
            "seo-analysis.json": state.analysis.model_dump_json(indent=2) if state.analysis else "null",
        }
        if state.combined_version:
            from src.services.website_package import files as website_files, handoff_index
            from src.services.presentation import render_current
            files.update(website_files(state,self.settings or Settings()))
            files['START_HERE.html']=handoff_index(state)
            for name in list(files):
                if name.startswith('drafts-html/'):
                    files[name]=render_current(state,self.settings or Settings())
            files['reviews/review-contract.json']=json.dumps(review_contract(state, self.settings or Settings()),ensure_ascii=False,indent=2)
            from src.services.qa_context import module_context
            from src.combined.editorial import rules
            cfg=self.settings or Settings()
            from src.services.qa_audit import audit
            files['reviews/deterministic-audit.json']=json.dumps(audit(state,cfg),ensure_ascii=False,indent=2)
            files['reviews/business-review-context.json']=json.dumps(module_context(
                rules(cfg,state.editorial['page_type']),state.editorial['modules'],
                [m['id'] for m in state.editorial['modules']]),ensure_ascii=False,indent=2)
            files['reviews/qa-manifest.json']=json.dumps(state.qa_manifest,ensure_ascii=False,indent=2)
            from src.services.link_policy import contract,followups
            files['reviews/internal-link-policy.json']=json.dumps({'contract':contract(state),'followups':followups(state)},ensure_ascii=False,indent=2)
            files['reviews/research-packet.json']=json.dumps(state.research_packet,ensure_ascii=False,indent=2)
            files['reviews/claim-source-sheet.json']=json.dumps(state.editorial.get('claims',[]),ensure_ascii=False,indent=2)
            files['reviews/release-blockers.json']=json.dumps(state.release_blockers,ensure_ascii=False,indent=2)
            files['reviews/professional-review.json'] = (state.professional_review.model_dump_json(indent=2)
                                                       if state.professional_review else 'null')
            files['briefings/infographic.json']=json.dumps({'run_id':str(state.run_id),'revision':state.revision,**state.editorial.get('graphic',{})},ensure_ascii=False,indent=2)
            files['drafts-markdown/content.md']='\n\n'.join('<!-- '+m['id']+' -->\n'+m['markdown'] for m in state.editorial.get('modules',[]))
            files['STATUS.txt']='MANUAL UPLOAD ONLY. '+('HUMAN APPROVED; NOT PUBLISHED.' if state.status=='exported' else 'UNAPPROVED REVIEW PREVIEW. DO NOT UPLOAD.')
            files['GOOGLE_DOCS_IMPORT.md'] = ('# Google Docs fallback\n\nUpload the HTML draft to Google Drive and open it with Google Docs, '
                'or paste the Markdown content into a document. Inspect headings, tables, links and the infographic manually. '
                'This package does not create a Google Doc or publish anything through an API.\n')
        b = state.briefing
        sections = '\n'.join('## '+h.h2+'\n'+ '\n'.join('### '+s for s in h.h3)+'\n'+h.guidance for h in b.headings)
        files['briefings/content-briefing.md'] = '# '+b.title+'\n\nZielgruppe: '+b.target_audience+'\n\nSuchintention: '+b.search_intent+'\n\nLeserziel: '+b.reader_outcome+'\n\nHauptkeyword: '+b.primary_keyword+'\n\nNebenkeywords: '+', '.join(b.secondary_keywords)+'\n\nUmfang: ca. '+str(b.suggested_words)+' Wörter\n\n'+sections+'\n\n## Interne Verlinkung\n'+ '\n'.join('- '+i for i in b.internal_link_ids)+'\n\n## CTA\n'+b.cta+'\n\n## FAQ\n'+'\n'.join('- '+q for q in b.faq_questions)+'\n\n## Quellen\n'+'\n'.join('- '+u for u in b.source_urls)+'\n\n## Fachliche Prüfung\n'+'\n'.join('- '+n for n in b.legal_review_notes)+'\n\n## Redaktionelle Hinweise\n'+'\n'.join('- '+n for n in b.editorial_notes)+'\n'
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(['source','target','anchor','reason'])
        for edge in state.topic_map.links:
            # Neutralize spreadsheet formulas when opening a CSV export.
            writer.writerow(["'"+x if x.startswith(('=','+','-','@')) else x for x in [edge.source,edge.target,edge.anchor,edge.reason]])
        files['topic-maps/internal-links.csv'] = csv_buffer.getvalue()
        files['topic-maps/internal-links.mmd'] = 'flowchart TD\n' + '\n'.join('  '+e.source.replace('-','_')+' --> '+e.target.replace('-','_') for e in state.topic_map.links)+'\n'
        if state.decision_workflow_version:
            files.update(self._decision_files(state))
        if state.research_snapshot:
            files['research-snapshot.json']=json.dumps(state.research_snapshot,ensure_ascii=False,indent=2)
            files['reviews/selected-evidence.json']=json.dumps(state.selected_evidence,ensure_ascii=False,indent=2)
        paths=self._write_files(destination, files)
        if state.combined_version:
            from src.combined.graphics import copy_approved
            graphic=copy_approved(state,destination)
            if graphic:paths.append(graphic)
        return paths

    def _decision_files(self, state):
        return {'seo-decisions.json': json.dumps({'recommendations': [r.model_dump(mode='json') for r in state.analysis.recommendations],
                'selected': state.selected_recommendation.model_dump(mode='json') if state.selected_recommendation else None,
                'selection_history': [e.model_dump(mode='json') for e in state.selection_history]}, ensure_ascii=False, indent=2),
                'seo-recommendations.html': render_recommendations(state)}

    @diagnostic_boundary('Local Export', 'Write planning bundle')
    def write_planning_bundle(self, state):
        if not state.analysis or not state.topic_map:
            raise ValueError('Planning evidence is incomplete')
        destination = self.root / 'runs' / str(state.run_id) / f'revision-{state.revision:03d}'
        files = self._decision_files(state)
        files.update({'seo-analysis.json': state.analysis.model_dump_json(indent=2),
                      'reviews/state.json': state.model_dump_json(indent=2),
                      'topic-maps/topic-map.json': state.topic_map.model_dump_json(indent=2),
                      'topic-maps/topic-map.html': render_topic_map(state.topic_map, verified_topic_links(state))})
        if state.research_snapshot:
            files['research-snapshot.json']=json.dumps(state.research_snapshot,ensure_ascii=False,indent=2)
        return self._write_files(destination, files)

    @staticmethod
    def _write_files(destination, files):
        paths=[]
        for rel, content in files.items():
            path = destination / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix+'.tmp')
            if isinstance(content,bytes):tmp.write_bytes(content)
            else:tmp.write_text(content,encoding='utf-8')
            tmp.replace(path)
            paths.append(str(path))
        return paths
