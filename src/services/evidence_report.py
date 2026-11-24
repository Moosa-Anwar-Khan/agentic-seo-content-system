"""English management summary and original-evidence appendix; no provider calls."""
import csv
import json
from html import escape
from io import BytesIO,StringIO
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4


def export_evidence(data):
    stream=StringIO();writer=csv.writer(stream)
    writer.writerow(['recommendation_id','topic','action','evidence_status','reason_en','keyword','source','country',
        'reporting_start','reporting_end','impressions','clicks','position','estimated_volume','page','website_evidence','query_wording_market','service_topic_group'])
    def safe(value):
        value='' if value is None else str(value)
        return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value
    for rec in data['recommendations']:
        for metric in rec.get('metrics') or [{}]:
            row=[rec['id'],rec['title'],rec['action'],rec.get('evidence_status','needs_research'),rec.get('reason_en',''),
                *[metric.get(k) for k in ['keyword','source','country','reporting_start','reporting_end','impressions','clicks','position','volume','page']],
                json.dumps(rec.get('website_evidence',[]),ensure_ascii=False),rec.get('search_market','unspecified'),rec.get('service_segment','general_tax')]
            writer.writerow([safe(x) for x in row])
    return stream.getvalue()


def english_report(data):
    stream=BytesIO();styles=getSampleStyleSheet()
    styles['BodyText'].fontSize=9;styles['BodyText'].leading=12
    styles['Title'].fontSize=20
    def p(text,style='BodyText'):return Paragraph(escape(str(text)),styles[style])
    backed=[r for r in data['recommendations'] if r.get('evidence_status')=='evidence_backed']
    budget=data.get('campaign') or {}
    if not budget:
        b=data['budget'];budget={k.replace('_usd','_microusd'):round(v*1000000) for k,v in b.items() if k in {'recorded_usd','reserved_usd','limit_usd'} and v is not None}
    story=[p('Project progress: search and AI visibility','Title'),p('Muster & Partner | Germany | Saved evidence, not a ranking guarantee'),Spacer(1,12),
        p('Current state','Heading2'),p(f"Run {data['run_id'][:8]} | {data['status']} | Evidence: {data['report_readiness']}"),
        p(f"Supported actions: {len(backed)}. Research is an observation of search performance; it does not establish improved rankings, revenue or professional approval."),
        p('Priorities supported by the current evidence','Heading2')]
    if not backed:story.append(p('No supported content action has been established. Resolve the evidence gaps before purchasing a draft.'))
    for number,rec in enumerate(backed[:5],1):
        story.extend([p(f"{number}. {rec['title'][:120]}"),p(rec.get('reason_en') or 'Inspect the cited original measurements and website text.')])
    story.extend([p('Spending and remaining requirements','Heading2'),
        p('Recorded: $%.4f | Reserved: $%.4f | Limit: %s' % (budget.get('recorded_microusd',0)/1e6,budget.get('reserved_microusd',0)/1e6,
            ('$%.2f'%(budget['limit_microusd']/1e6)) if budget.get('limit_microusd') else 'Not recorded')),
        p('Provider billing is separately reconcilable. Infographic, professional attestation and final approval remain required before local release. Publication is manual.'),
        p('The appendix contains original German titles and quotations. AI observations must be reviewed separately by platform and evidence type.'),
        PageBreak(),p('Evidence appendix','Title')])
    for rec in data['recommendations']:
        story.extend([p(rec['title'],'Heading2'),p(rec.get('reason_en') or 'Original recommendation requires evidence review.'),
            p('Evidence status: '+rec.get('evidence_status','needs_research')),p('Target: '+str(rec.get('target_url') or rec.get('parent_url') or 'Not established'))])
        for metric in rec.get('metrics',[]):
            story.append(p(json.dumps(metric,ensure_ascii=False)))
        for evidence in rec.get('website_evidence',[]):
            story.extend([p('Original German quotation: '+evidence['quote']),p(evidence['url']),p('Retrieved: '+str(evidence.get('retrieved_at')))])
        if rec.get('uncertainties'):story.append(p('Original research limitations: '+'; '.join(rec['uncertainties'])))
    SimpleDocTemplate(stream,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=35,bottomMargin=35,
        title='Muster project progress and research evidence').build(story)
    return stream.getvalue()
