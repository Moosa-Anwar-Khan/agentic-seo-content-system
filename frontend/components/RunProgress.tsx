import QAFindingList, { type PortalFinding } from './QAFindingList';
import { type WorkflowSummary } from './GuidedWorkflow';
import ErrorNotice from './ErrorNotice';
import FailureHistory from './FailureHistory';
import { type FailureSummary } from '../lib/api';
import { t } from '../lib/ui';

export type RunProgressData={current_failure?:FailureSummary|null;run_id:string;revision:number;state:string;next_action:string;updated_at:string;tokens_used:number;
 check_status?:{brand:string;seo:string;draft:string;summary:string};
 design_status?:{branding_version:string;revision:number;branding?:{status:string;message:string;profile:string;version:string};content_notes?:{module:string;message:string}[];wordpress:{status:string;message:string;reason:string}};
 workflow?:WorkflowSummary;qa_readiness?:{ready:boolean;reason:string;blocked_units?:string[]};
 findings?:PortalFinding[];
 deterministic_audit?:{version:string;approved_matches:{module_id:string;approved_form:string;variant:string}[];link_counts:{module_id:string;unique_verified_destinations:number}[];limitations:string}|null;
 review_plan?:{remaining?:number;maximum_reservation_microusd?:number;units?:{unit_id:string;cached:boolean;maximum_reservation_microusd:number}[];blocked?:boolean;message?:string};
 editorial_followups?:{code:string;message:string;action:string}[];
 preflight?:{status:string;missing_research:string[];model_routes:Record<string,{provider:string;model:string}>};
 internal_link_policy?:{policy:string};
 qa:{completed:number;total:number;complete:boolean;current_unit:string|null};
 latest_attempt:{status:string;step:string;updated_at:string}|null;
 costs:{recorded_microusd:number;reserved_microusd:number;limit_microusd:number;provider_reported_usd:number;provider_costs_incomplete:boolean}};

// Facts first, prose behind one expandable details block.
export default function RunProgress({data,admin=false,onShow}:{data:RunProgressData;admin?:boolean;onShow?:(finding:PortalFinding)=>void}) {
 const usd=(value:number)=>(value/1000000).toFixed(4)+' USD';
 return <section id="guided-qa" className="panel" aria-label={t('current_progress')} aria-live="polite">
  <h2>{t('current_progress')}: {data.state}</h2>
  {data.current_failure&&<details><summary>Review details and debugging reference</summary><ErrorNotice admin={admin} info={{code:data.current_failure.code,title:data.current_failure.component+' — '+(data.current_failure.category==='content_corrections'?t('needs_correction'):t('step_stopped')),message:data.current_failure.reason,next_steps:[data.current_failure.next_action],retry:data.current_failure.retry,reference:data.current_failure.reference,failure:data.current_failure}}/></details>}
  <p>{data.next_action}</p>
  {data.check_status&&<p data-testid="check-status-summary"><strong>{data.check_status.summary}</strong></p>}
  {data.design_status&&<aside aria-label="Website design status"><p><strong>{data.design_status.branding?.message||'Page-specific branding applied'} · Version {data.design_status.branding_version}</strong> <span className="hint">{t('design_preview_hint')}</span></p><details><summary>Website import details</summary><p><strong>{data.design_status.wordpress.message}</strong></p><p>{data.design_status.wordpress.reason} This does not block viewing or downloading. A private import test is still needed before claiming compatibility with the website.</p><p>Content revision {data.design_status.revision}</p></details></aside>}
  {!!data.findings?.length&&<QAFindingList findings={data.findings} onShow={onShow}/>}
  <p className="facts-strip"><span>{t('revision')} {data.revision}</span><span>{t('tokens')} {data.tokens_used.toLocaleString('en-GB')}</span>{data.qa.total>0&&<span>{t('review_units')}: {data.qa.completed} / {data.qa.total}</span>}<span>{t('recorded_run_cost')} <strong>{usd(data.costs.recorded_microusd)}</strong></span><span>{t('reserved')} {usd(data.costs.reserved_microusd)}</span><span>{t('run_allowance')} {usd(data.costs.limit_microusd)}</span><span>{t('provider_reported')}: {data.costs.provider_reported_usd.toFixed(4)} USD{data.costs.provider_costs_incomplete?' · '+t('partial_cost_data'):''}</span></p>
  <p className="hint">{t('cost_reservation_help')}</p>
  <details><summary>Details: checks, configuration and spending</summary>
  {data.review_plan&&!data.review_plan.blocked&&<details><summary>Review units and estimated reservations</summary><p>Only unfinished or changed units need another request. A reservation is a conservative allowance, not an invoice.</p><ul>{data.review_plan.units?.map(item=><li key={item.unit_id}>{item.unit_id}: {item.cached?'saved result reusable':usd(item.maximum_reservation_microusd)+' maximum reservation'}</li>)}</ul></details>}
  {data.deterministic_audit&&<details><summary>Verified wording and link counts</summary><p>{data.deterministic_audit.limitations}</p><ul>{data.deterministic_audit.approved_matches.map((item,index)=><li key={index}>{item.module_id}: approved {item.variant} form — <span lang="de">{item.approved_form}</span></li>)}</ul><ul>{data.deterministic_audit.link_counts.filter(item=>item.unique_verified_destinations>0).map(item=><li key={item.module_id}>{item.module_id}: {item.unique_verified_destinations} unique verified link destinations; relevance requires semantic review.</li>)}</ul></details>}
  {data.internal_link_policy&&<p className="hint">{t('internal_link_policy')}: {data.internal_link_policy.policy==='starter_library'?t('starter_library'):t('strict_link_policy')}</p>}
  {data.preflight&&<div><h3>{t('preflight')}</h3><p>{t(data.preflight.status==='ready'?'preflight_ready':data.preflight.status==='blocked'?'preflight_blocked':'preflight_research')}</p>
    {data.preflight.missing_research.length>0&&<ul>{data.preflight.missing_research.map(item=><li key={item}>{item}</li>)}</ul>}
    <details><summary>{t('model_routes')}</summary><ul>{Object.entries(data.preflight.model_routes).map(([task,route])=><li key={task}>{task.replaceAll('_',' ')}: {route.provider}/{route.model}</li>)}</ul></details></div>}
  {!!data.editorial_followups?.length&&<details><summary>{t('editorial_followups')}</summary>{data.editorial_followups.map(item=><p key={item.code}>{item.message} {item.action}</p>)}</details>}
  {!!data.design_status?.content_notes?.length&&<details><summary>Saved content notes before release</summary><p>These are separate from styling. No reviewed text was changed.</p>{data.design_status.content_notes.map(item=><p key={item.module}>{item.module}: {item.message}</p>)}</details>}
  {admin&&<FailureHistory key={data.run_id} runId={data.run_id}/>}
  </details>
  <p className="hint">{t('updated_at')}: {new Date(data.updated_at).toLocaleString('en-GB')}. {t('history_explanation')}</p>
 </section>;
}
