'use client';
import { t, portalCopy } from '../lib/ui';

export type StatusBarCosts = {recorded_microusd:number;reserved_microusd:number;limit_microusd:number};

// Presentational only: clicking a step navigates; navigation never starts paid work.
export default function RunStatusBar({runId,revision,status,costs,currentStage,suggestedStage,onNavigate}:
  {runId:string;revision:number;status:string;costs:StatusBarCosts|null;currentStage:number;suggestedStage:number;onNavigate:(stage:number)=>void}) {
  const usd=(value:number)=>(value/1000000).toFixed(4)+' USD';
  return <div className="run-status-bar">
    <div className="run-chip"><strong>{runId.slice(0,8)}</strong>{t("revision")} {revision}<span className="badge">{status}</span></div>
    <nav className="status-steps" data-run-id={runId} aria-label={t("run_stages_nav")} title={t("navigation_does_not_start_paid_work")}>
      {portalCopy.stages.map((label,index)=>{
        const state=index===currentStage?'current':index<suggestedStage?'complete':'upcoming';
        return <button type="button" key={label} className={'status-step is-'+state}
          aria-current={index===currentStage?'step':undefined}
          aria-label={state==='complete'?label+' ('+t("completed")+')':label}
          onClick={()=>onNavigate(index)}>
          <span aria-hidden="true">{state==='complete'?'✓':index+1}</span>{label}
        </button>;})}
    </nav>
    {costs&&<p className="cost-chip">{t("recorded")} {usd(costs.recorded_microusd)} · {t("reserved")} {usd(costs.reserved_microusd)} · {t("run_allowance")} {usd(costs.limit_microusd)}</p>}
  </div>;
}
