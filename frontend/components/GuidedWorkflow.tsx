'use client';
import { t } from '../lib/ui';
export type WorkflowSummary={current_step:string;workspace:number;saved_work:string;steps:{id:string;label:string;workspace:number;status:string}[];primary_action:{id:string;label:string;enabled:boolean;cost:string;explanation:string};permissions:{draft:boolean;professional:boolean;final:boolean};release_requirements:string[]};
export type StatusChip={label:string;kind:'pass'|'warn'|'fail'|'info'};

// One glance, one action: current state as chips, the single next action, costs.
export default function GuidedWorkflow({data,chips,stateLabel,costs,onNavigate,onAction}:
  {data:WorkflowSummary;chips?:StatusChip[];stateLabel?:string;costs?:{recorded_microusd:number;reserved_microusd:number;limit_microusd:number}|null;
   onAction?:()=>void;onNavigate:(workspace:number,step?:string)=>void}) {
 const action=data.primary_action;
 const usd=(value:number)=>(value/1000000).toFixed(4)+' USD';
 return <section className="panel guided-workflow" aria-label="Your next step">
  {stateLabel&&<p className="gw-state">{stateLabel}</p>}
  <h2>{data.steps.find(step=>step.id===data.current_step)?.label}: your next step</h2>
  <p className="gw-summary">{data.saved_work}</p>
  {!!chips?.length&&<p className="chip-row">{chips.map((chip,index)=><span key={index} className={'chip chip-'+chip.kind}>{chip.label}</span>)}</p>}
  <nav aria-label="Workflow stages" className="guided-steps">{data.steps.map((step,index)=><button type="button" key={step.id} aria-current={step.id===data.current_step?'step':undefined} onClick={()=>onNavigate(step.workspace,step.id)}><span aria-hidden="true">{step.status==='complete'?'✓':index+1}</span> {step.label}<small>{step.status}</small></button>)}</nav>
  <div className="gw-action"><button disabled={!action.enabled} aria-describedby="next-action-help" onClick={()=>onAction?onAction():onNavigate(data.workspace,data.current_step)}>{action.label} · {action.cost}</button><p id="next-action-help" className="hint">{action.explanation}</p></div>
  {costs&&<p className="gw-costs">{t('recorded')} {usd(costs.recorded_microusd)} · {t('reserved')} {usd(costs.reserved_microusd)} · {t('run_allowance')} {usd(costs.limit_microusd)}</p>}
  <p className="hint">Opening a step never starts paid work. German content remains unchanged.</p>
 </section>;
}
