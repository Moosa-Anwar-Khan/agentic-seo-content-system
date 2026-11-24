'use client';
import { t } from '../../lib/ui';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { portalFetch,errorInfo,type UserError } from '../../lib/api';
import ErrorNotice from '../../components/ErrorNotice';
type Readiness={status:string;label:string;mode:string;checked_at:string;live_validation:string;checks:{id:string;label:string;status:string;message:string}[]};
export default function SystemStatus(){
 const [report,setReport]=useState<Readiness|null>(null),[error,setError]=useState<UserError|null>(null),[busy,setBusy]=useState(false);const pending=useRef(false);
 async function refresh(){if(pending.current)return;pending.current=true;setBusy(true);setError(null);try{setReport(await(await portalFetch('/system/readiness')).json());}catch(e){setError(errorInfo(e));}finally{pending.current=false;setBusy(false);}}
 useEffect(()=>{void refresh();},[]);
 return <main><header className="management-header"><div><p className="eyebrow">{t("configuration_confidence")}</p><h1>{t("system_status")}</h1><p>{t("local_checks_before_you_start_no_paid_requests_are_made_here")}</p></div><button disabled={busy} onClick={refresh}>{busy?'Checking…':t("refresh_checks")}</button></header><ErrorNotice info={error} onDismiss={()=>setError(null)}/>{busy&&!report&&<p role="status">{t("checking_local_configuration")}</p>}{report&&<><section className="panel status-card"><div><span className={'badge '+(report.status==='ready'?'pass':'warning')}>{report.mode==='demo'?t("offline_demo"):t("live_mode_configured")}</span><h2>{report.label}</h2><p>{report.live_validation}</p><small>{t("checked")} {new Date(report.checked_at).toLocaleString('en-GB')}</small></div><Link href="/">{t("open_workspace")}</Link></section><div className="readiness-grid">{report.checks.map(check=><article className="readiness-card" key={check.id}><span className={'badge '+check.status}>{check.status==='pass'?t("local_check_passed"):check.status==='warning'?t("review_recommended"):t("action_required")}</span><h2>{check.label}</h2><p>{check.message}</p></article>)}</div><section className="panel"><h2>{t("live_validation_remains_separate")}</h2><p>{t("these_checks_do_not_verify_provider_credits_website_permissions_o")}</p></section></>}</main>;
}
