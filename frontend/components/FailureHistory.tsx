'use client';
import { useState } from 'react';
import { portalFetch, type FailureSummary } from '../lib/api';
import FailureDetails from './FailureDetails';
import { t } from '../lib/ui';
type Event={id:string;summary:FailureSummary;technical:unknown};
export default function FailureHistory({runId}:{runId:string}) {
 const [events,setEvents]=useState<Event[]|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(false);
 async function load(){if(busy)return;setBusy(true);setError(false);try{const response=await portalFetch('/runs/'+runId+'/diagnostics');setEvents((await response.json()).events);}catch{setError(true);}finally{setBusy(false);}}
 return <details><summary>{t('failure_history')}</summary><button className="secondary" disabled={busy} onClick={()=>void load()}>{t('load_failure_history')}</button>{error&&<p role="status">{t('diagnostics_unavailable')}</p>}{events?.length===0&&<p>{t('no_failure_records')}</p>}{events?.map(event=><details key={event.id}><summary>{event.summary.component} · {event.summary.occurred_at} · {event.id}</summary><p>{event.summary.reason}</p><FailureDetails failure={event.summary} admin report={event}/></details>)}</details>;
}
