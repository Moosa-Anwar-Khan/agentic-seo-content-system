'use client';
import ReviewNote from '../components/ReviewNote';
import { t, visibilityCopy } from '../lib/ui';
import { useState } from 'react';

export type Recommendation = {
  search_market?:'hamburg'|'national_online'|'unspecified';service_segment?:'property_investors'|'specialist_tax'|'general_tax';
  profile_ready?:boolean;
  evidence_status?:'evidence_backed'|'needs_research';reason_en?:string;next_action_en?:string;
  id:string;topic_id:string;title:string;keyword:string;search_intent:string;
  page_type:'landing_page'|'blog';action:'protect'|'optimize'|'create_cluster'|'create_new'|'manual_review'|'investigate';
  research_needed?:boolean;drafting_eligible?:boolean;links_ready?:boolean;readiness_blockers?:string[];next_steps?:string[];supported_changes?:{source_url:string;target_url:string;evidence_quote:string;anchor:string;reasoning:string}[];
  target_url:string|null;parent_url:string|null;evidence_refs:string[];reasoning:string;
  uncertainties:string[];demo:boolean;
  internal_link_suggestions:{source_url:string;target_url:string;anchor:string;reasoning:string}[];
};
export type Research = {
  data_mode:string;recommendations:Recommendation[];limitations:string[];
  keywords:{keyword:string;source:string;volume:number|null;difficulty:number|null;position:number|null;
    clicks:number|null;impressions:number|null;ctr:number|null;page:string|null;reporting_start:string|null;reporting_end:string|null}[];
  page_assessments?:{topic_id:string;page_url:string|null;relation:string;evidence_quote:string;issue:string;distinct_need:string;reasoning:string}[];
  page_evidence?:{url:string;title:string;indexable?:boolean|null}[];
};
export type Selection = {recommendation_id:string;expected_revision:number;mode:'draft'|'links_only';
  override_protection:boolean;acknowledge_uncertainty:boolean;notes:string;professional_support?:boolean;verified_parent_url?:string|null};
const labels:Record<Recommendation['action'],string>={protect:t("protect_existing_page"),optimize:t("review_potential_revision"),
  create_cluster:t("create_a_supporting_article"),create_new:t("create_a_new_page"),manual_review:t("manual_review_required"),investigate:t("investigate_visibility")};
const intents:Record<string,string>={commercial:t("find_professional_advice"),informational:t("find_information"),transactional:t("make_an_enquiry"),navigational:t("find_a_specific_page")};

export default function SEORecommendations({analysis,revision,disabled,onSelect}:{analysis:Research;revision:number;disabled:boolean;onSelect:(selection:Selection)=>Promise<void>}) {
  const [selected,setSelected]=useState('');
  const [notes,setNotes]=useState('');
  const [override,setOverride]=useState(false);
  const [acknowledged,setAcknowledged]=useState(false);
  const [professional,setProfessional]=useState(false);
  const [parentUrl,setParentUrl]=useState('');
  const chosen=analysis.recommendations.find(r=>r.id===selected);
  const uncertain=!!chosen&&(chosen.action==='manual_review'||chosen.uncertainties.length>0);
  const draftAllowed=!!chosen&&chosen.profile_ready!==false&&(!uncertain||acknowledged)&&
    (chosen.action!=='protect'||override)&&(!(uncertain||override)||notes.trim().length>=10)&&
    (chosen.drafting_eligible!==false||(professional&&notes.trim().length>=40))&&
    (chosen.drafting_eligible!==false||!!chosen.target_url||!!chosen.parent_url||!!parentUrl);
  async function select(mode:'draft'|'links_only') {
    if(!chosen)return;
    await onSelect({recommendation_id:chosen.id,expected_revision:revision,mode,
      override_protection:mode==='draft'&&override,acknowledge_uncertainty:mode==='draft'&&acknowledged,notes,professional_support:mode==='draft'&&professional,verified_parent_url:parentUrl||null});
  }
  return <section className="panel"><h2>{t("choose_an_seo_recommendation")}</h2>
    <p>{t("research_is_saved_briefing_and_drafting_begin_only_after_your_sel")}</p>
    {analysis.data_mode==='demo'&&<p className="hint">{t("demo_examples_only_no_live_research_or_newly_measured_metrics_rev")}</p>}
    <details><summary>{t("evidence_limitations")}</summary><ul>{analysis.limitations.map((note,i)=><li key={i}>{note}</li>)}</ul></details>
    {analysis.recommendations.map(item=><article className={'asset recommendation-card'+(selected===item.id?' is-selected':'')} key={item.id}>
      <div className="recommendation-card-header">
        <span id={'output-type-'+item.id} className={'recommendation-type '+(item.page_type==='blog'?'is-blog':'is-service')}>
          {t('generates_label')} <strong>{t(item.page_type==='blog'?'blog_article':'service_page')}</strong>
        </span>
        {selected===item.id&&<span className="recommendation-selected">{t('selected_label')}</span>}
      </div>
      <p className="recommendation-type-help" id={'output-help-'+item.id}>{t(item.page_type==='blog'?'blog_article_explanation':'service_page_explanation')}</p>
      <label><input type="radio" name="seo-recommendation" aria-describedby={'output-type-'+item.id+' output-help-'+item.id} value={item.id} checked={selected===item.id}
        disabled={disabled} onChange={()=>{setSelected(item.id);setNotes('');setOverride(false);setAcknowledged(false);setProfessional(false);setParentUrl('');}}/>
        {item.title} · {labels[item.action]}</label>
      <p>{t("keyword")} <strong>{item.keyword}</strong> {t("search_intent")} {intents[item.search_intent]||item.search_intent}</p>
      {item.search_market&&item.service_segment&&<p className="hint">{visibilityCopy.market}: {visibilityCopy[item.search_market]} · {visibilityCopy[item.service_segment]}</p>}
      <p><span className={'evidence-badge '+(item.evidence_status==='evidence_backed'?'supported':'pending')}>{t(item.evidence_status==='evidence_backed'?'evidence_backed':'needs_more_research')}</span></p>
      <p><strong>{t('why_recommended')}: </strong>{item.reason_en||t('needs_more_research')}</p>
      {item.next_action_en&&<p>{item.next_action_en}</p>}
      <details><summary>{t('original_german_research_notes')}</summary><p lang="de">{item.reasoning}</p></details>
      {!!item.readiness_blockers?.length&&<details><summary>{t("why_is_drafting_blocked")}</summary><ul>{item.readiness_blockers.map((b,i)=><li key={i}><ReviewNote text={b}/></li>)}</ul><ul>{item.next_steps?.map((n,i)=><li key={i}>{n}</li>)}</ul></details>}
      {item.links_ready&&<p className="research-notice">{t("an_evidence_backed_link_suggestion_is_available_save_it_for_manua")}</p>}
      {item.supported_changes?.map((c,i)=><blockquote key={i}><p>{t("existing_german_text")}{c.evidence_quote}“</p><p><a href={c.source_url} target="_blank" rel="noreferrer">{t("source_page")}</a> → <a href={c.target_url} target="_blank" rel="noreferrer">{c.anchor}</a></p><p>{c.reasoning}</p></blockquote>)}
      {[item.target_url,item.parent_url].filter(Boolean).map(url=><p key={url}><a href={url!} target="_blank" rel="noreferrer">{t("existing_page")} {url}</a></p>)}
      {item.uncertainties.length>0&&<ul>{item.uncertainties.map((note,i)=><li key={i}>{note}</li>)}</ul>}
      <details><summary>{t("show_proof")}</summary>
        {analysis.keywords.map((metric,index)=>item.evidence_refs.includes('keyword-'+index)&&<div key={index} className="asset">
          <p>{metric.keyword} · {metric.source}{metric.reporting_start&&metric.reporting_end?' · '+metric.reporting_start+t("to_2")+metric.reporting_end:''}</p>
          {metric.page&&<p>{metric.page}</p>}
          <p>{t("impressions_2")} {metric.impressions??t("not_measured")} {t("clicks_2")} {metric.clicks??t("not_measured")} {t("average_position_2")} {metric.position??t("not_measured")}  {t("ctr")} {metric.ctr===null?t("not_measured"):(metric.ctr*100).toFixed(2)+' %'}</p>
          <p>{t("search_volume")} {metric.volume??t("not_measured")} {t("difficulty")} {metric.difficulty??t("not_measured")}</p>
        </div>)}
        {analysis.page_assessments?.filter(a=>a.topic_id===item.topic_id&&a.page_url).map((a,i)=><blockquote key={i}><p>{a.evidence_quote}</p><p>{a.reasoning}</p>{a.issue&&<p>{t("review_point")} {a.issue}</p>}{a.distinct_need&&<p>{t("additional_reader_need")} {a.distinct_need}</p>}</blockquote>)}
        <p className="hint">{t("positions_are_averages_for_the_reported_query_url_data_there_is_n")}</p>
      </details>
      {item.internal_link_suggestions.length>0&&<details><summary>{t("internal_link_suggestions")}</summary>{item.internal_link_suggestions.map((link,i)=><p key={i}><a href={link.source_url} target="_blank" rel="noreferrer">{t("source_page")}</a> → <a href={link.target_url} target="_blank" rel="noreferrer">{link.anchor}</a> · {link.reasoning}</p>)}</details>}
    </article>)}
    {chosen&&<div>
      {chosen.drafting_eligible===false&&!chosen.target_url&&!chosen.parent_url&&<label>{t("verified_parent_page")}<select value={parentUrl} onChange={e=>setParentUrl(e.target.value)}><option value="">{t("select_a_professionally_verified_parent_page")}</option>{analysis.page_evidence?.filter(p=>p.indexable!==false).map(p=><option key={p.url} value={p.url}>{p.title||p.url}</option>)}</select></label>}
      {chosen.drafting_eligible===false&&chosen.profile_ready!==false&&<><p className="research-notice">{t("more_research_is_required_before_drafting_professional_support_ne")}</p><label><input type="checkbox" checked={professional} onChange={e=>setProfessional(e.target.checked)}/>{t("i_confirm_professional_support_for_the_draft_and_have_documented_")}</label></>}
      {chosen.action==='protect'&&<label><input type="checkbox" checked={override} onChange={e=>setOverride(e.target.checked)}/>{t("i_want_to_correct_the_protected_page_for_a_specific_documented_re")}</label>}
      {uncertain&&<label><input type="checkbox" checked={acknowledged} onChange={e=>setAcknowledged(e.target.checked)}/>{t("i_have_reviewed_the_open_assumptions_and_confirm_creation_of_a_pr")}</label>}
      <label>{t("reasoning_and_instructions")}{(uncertain||override)&&t("at_least_10_characters")}<textarea rows={3} maxLength={3000} value={notes} onChange={e=>setNotes(e.target.value)} placeholder={t("do_not_enter_client_information")}/></label>
      <div className="selected-output" role="status" aria-live="polite" aria-atomic="true">
        <p>{t('selected_draft_output')} <strong>{t(chosen.page_type==='blog'?'blog_article':'service_page')}</strong></p>
        <p className="hint">{t(chosen.page_type==='blog'?'blog_article_explanation':'service_page_explanation')}</p>
        <p className="hint">{t('draft_type_link_only_help')}</p>
      </div>
      {(chosen.target_url||chosen.parent_url)&&<button disabled={disabled} onClick={()=>select('links_only')}>{t("save_link_recommendations_only")}</button>}
      <button disabled={disabled||!draftAllowed} onClick={()=>select('draft')}>{chosen.action==='protect'?t("start_correction_draft_with_reasoning"):t("create_briefing_and_draft")}</button>
      <p className="hint">{t("wordpress_remains_unchanged_seo_evidence_does_not_verify_tax_accu")}</p>
    </div>}
  </section>;
}
