'use client';
import { useState } from 'react';
import { portalFetch, type FailureSummary } from '../lib/api';
import { t } from '../lib/ui';

export default function FailureDetails({failure,admin=false,report}:{failure:FailureSummary;admin?:boolean;report?:unknown}) {
 const [data,setData]=useState<unknown>(report),[open,setOpen]=useState(false),[busy,setBusy]=useState(false),[notice,setNotice]=useState('');
 async function inspect(){
  if(busy)return;
  if(open){setOpen(false);return;}
  setBusy(true);setNotice('');
  try {
   if(!data&&failure.job_id){const response=await portalFetch('/jobs/'+failure.job_id+'/diagnostics');setData(await response.json());}
   setOpen(true);
  }catch {setNotice(t('diagnostics_unavailable'));}finally{setBusy(false);}
 }
 async function copy(){try{await navigator.clipboard.writeText(JSON.stringify(data,null,2));setNotice(t('diagnostics_copied'));}catch{setNotice(t('diagnostics_copy_failed'));}}
 return <div className="failure-details">
  <p><strong>{t('failed_component')}:</strong> {failure.component} · {failure.category.replaceAll('_',' ')}</p>
  <p><strong>{t('failed_operation')}:</strong> {failure.operation}{failure.unit_id?' · '+failure.unit_id:''}{failure.asset_id?' · '+failure.asset_id:''}</p>
  {failure.revision!=null&&<p className="hint">{t('revision')} {failure.revision}{failure.occurred_at?' · '+new Date(failure.occurred_at).toLocaleString('en-GB'):''}</p>}
  <p>{failure.saved_work}</p>
  {admin&&(!!failure.job_id||!!report)&&<><button className="secondary" disabled={busy} onClick={()=>void inspect()}>{t('technical_details')}</button>{open&&<div><button className="secondary" onClick={()=>void copy()}>{t('copy_diagnostic_report')}</button><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:360,overflow:'auto'}}>{JSON.stringify(data,null,2)}</pre></div>}{notice&&<p role="status">{notice}</p>}</>}
 </div>;
}
