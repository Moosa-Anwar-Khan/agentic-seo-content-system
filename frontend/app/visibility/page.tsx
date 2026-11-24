'use client';
import {useEffect,useRef,useState} from 'react';
import Link from 'next/link';
import {portalFetch,PortalError,errorInfo,type UserError} from '../../lib/api';
import ErrorNotice from '../../components/ErrorNotice';
import {visibilityCopy as c} from '../../lib/ui';

type Run={run_id:string;status:string;revision:number};
type Observation={id:string;platform:string;query:string;model:string;search_mode:string;evidence_type:string;observed_at:string;excerpt:string;cited_urls:string[];provenance:string;brand_mentioned:boolean;collection_method?:string};
type Report={content_audit?:{pages_inspected:number;business_identity_pages:number;question_heading_pages:number;structured_data_inspected_pages:number;attribution_candidate_pages:number;official_link_pages:number;structured_data_problem_pages:number;limitations:string[]};queries:{id:number;query:string;category:string}[];platforms:{platform:string;automation:string;status:string;limitations:string}[];observations:Observation[];tools:{tool:string;enabled:boolean;configured:boolean;live_status:string;reason:string;next_action:string;last_verified_at:string|null}[];campaign:{recorded_microusd:number;reserved_microusd:number;limit_microusd:number;checkpoint_reached:boolean}|null};
type Job={id:string;status:string;current_step:string;error_info?:UserError|null};
export default function Visibility(){
 const [runs,setRuns]=useState<Run[]>([]),[runId,setRunId]=useState(''),[report,setReport]=useState<Report|null>(null);
 const [error,setError]=useState<UserError|null>(null),[busy,setBusy]=useState(false),[last,setLast]=useState(''),[job,setJob]=useState<Job|null>(null),[input,setInput]=useState('');
 const pending=useRef(false),generation=useRef(0),requestKey=useRef<string|null>(null);
 async function load(id:string,version=generation.current){
  try{
   const data=await(await portalFetch(`/runs/${id}/visibility`)).json();
   const jobs: (Job&{run_id:string})[]=await(await portalFetch('/jobs')).json();
   if(version!==generation.current)return;
   setReport(data);setLast(new Date().toISOString());setError(null);
   setJob(jobs.find(j=>j.run_id===id&&['queued','running','paused','failed'].includes(j.status))||null);
  }catch(e){if(version===generation.current){setError(errorInfo(e));if(e instanceof PortalError&&[401,403,404].includes(e.status)){setReport(null);setRuns([]);setJob(null);}}}
 }
 useEffect(()=>{let active=true;portalFetch('/runs').then(r=>r.json()).then((data:Run[])=>{if(active){setRuns(data);setRunId(data[0]?.run_id||'');}}).catch(e=>setError(errorInfo(e)));return()=>{active=false;};},[]);
 useEffect(()=>{generation.current++;setReport(null);setJob(null);setInput('');requestKey.current=null;if(runId)void load(runId);return()=>{generation.current++;};},[runId]);
 useEffect(()=>{if(!runId||!job||!['queued','running'].includes(job.status))return;const timer=setInterval(()=>{void load(runId);},4000);return()=>clearInterval(timer);},[runId,job?.status]);
 async function collect(){
  if(pending.current||!runId||!window.confirm(c.confirm))return;
  pending.current=true;setBusy(true);const version=generation.current;
  requestKey.current??=crypto.randomUUID();
  try{const next=await(await portalFetch('/jobs',{method:'POST',body:JSON.stringify({kind:'ai_visibility',run_id:runId,idempotency_key:requestKey.current})})).json();if(version===generation.current){setJob(next);requestKey.current=null;setError(null);}}
  catch(e){if(version===generation.current)setError(errorInfo(e));}
  finally{pending.current=false;setBusy(false);}
 }
 async function save(){
  if(pending.current||!runId)return;pending.current=true;setBusy(true);
  try{const data=JSON.parse(input);data.id??=crypto.randomUUID();setInput(JSON.stringify(data,null,2));await portalFetch(`/runs/${runId}/visibility/observations`,{method:'POST',body:JSON.stringify(data)});setInput('');await load(runId);}
  catch(e){setError(e instanceof SyntaxError?{code:'validation',title:c.invalid,message:c.importHelp,next_steps:[c.importHelp],retry:'correct_input'}:errorInfo(e));}
  finally{pending.current=false;setBusy(false);}
 }
 async function download(path:string,name:string){try{const data=await(await portalFetch(path)).blob();const address=URL.createObjectURL(data);const a=document.createElement('a');a.href=address;a.download=name;a.click();URL.revokeObjectURL(address);}catch(e){setError(errorInfo(e));}}
 const active=busy||!!job&&['queued','running','paused'].includes(job.status);
 const money=(value:number)=>'$'+(value/1e6).toFixed(4);
 return <main className="management visibility-management"><header className="management-header"><div><p className="eyebrow">Muster &amp; Partner</p><h1>{c.title}</h1><p>{c.intro}</p></div><Link href="/">{c.savedRun}</Link></header>
  <ErrorNotice info={error} onDismiss={()=>setError(null)}/>
  {!runs.length&&<section className="panel"><p>{c.empty}</p></section>}
  {!!runs.length&&<section className="panel"><label>{c.savedRun}<select value={runId} disabled={busy} onChange={e=>setRunId(e.target.value)}>{runs.map(r=><option key={r.run_id} value={r.run_id}>{r.run_id.slice(0,8)} · {r.status.replaceAll('_',' ')} · {r.revision}</option>)}</select></label><div className="actions"><button disabled={busy} onClick={()=>void load(runId)}>{c.refresh}</button><button disabled={active} onClick={collect}>{c.collect}</button><button className="secondary" onClick={()=>void download(`/runs/${runId}/research-evidence?format=csv`,'research-evidence.csv')}>{c.download}</button><button className="secondary" onClick={()=>void download(`/runs/${runId}/analysis-report.pdf?scope=site&language=en`,'Muster_Research_Progress.pdf')}>{c.report}</button></div>{last&&<p className="connection-meta">{c.lastRefresh}: {new Date(last).toLocaleString('en-GB')}</p>}{job&&<p role="status">{c.job}: {job.status} · {job.current_step}</p>}{job?.error_info&&<ErrorNotice info={job.error_info}/>}</section>}
  {report&&<>
   {report.campaign&&<section className="panel"><h2>{c.campaign}</h2><p>{c.recorded}: {money(report.campaign.recorded_microusd)} · {c.reserved}: {money(report.campaign.reserved_microusd)} · {c.limit}: {money(report.campaign.limit_microusd)}</p>{report.campaign.checkpoint_reached&&<p className="research-notice">{c.checkpoint}</p>}</section>}
   <section className="panel"><h2>{c.tools}</h2><p>{c.toolHelp}</p><div className="readiness-grid">{report.tools.map(tool=><article className="readiness-card" key={tool.tool}><h3>{tool.tool}</h3><p><strong>{tool.enabled?c.enabled:c.disabled}</strong> · {tool.configured?c.configured:c.missing} · {tool.live_status==='unverified'?c.notVerified:tool.live_status}</p><p>{tool.reason}</p>{tool.last_verified_at&&<small>{new Date(tool.last_verified_at).toLocaleString('en-GB')}</small>}<p>{c.next}: {tool.next_action}</p></article>)}</div></section>
   <section className="panel"><h2>{c.platform}</h2><p>{c.disclaimer}</p><div className="table-scroll visibility-platforms"><table><thead><tr><th>{c.platform}</th><th>{c.automation}</th><th>{c.status}</th><th>{c.limitations}</th></tr></thead><tbody>{report.platforms.map(p=><tr key={p.platform}><td data-label={c.platform}>{p.platform}</td><td data-label={c.automation}>{p.automation==='controlled_api'?c.controlled:c.manual}</td><td data-label={c.status}>{p.status==='observed'?c.observed:c.notTested}</td><td data-label={c.limitations}>{p.limitations}</td></tr>)}</tbody></table></div></section>
   {report.content_audit&&<section className="panel"><h2>{c.audit}</h2><p>{c.auditHelp}</p><dl className="visibility-audit">{([
    [c.inspectedPages,report.content_audit.pages_inspected],[c.identityPages,report.content_audit.business_identity_pages],
    [c.questionPages,report.content_audit.question_heading_pages],[c.schemaPages,report.content_audit.structured_data_inspected_pages],
    [c.attributionPages,report.content_audit.attribution_candidate_pages],[c.officialPages,report.content_audit.official_link_pages],
    [c.schemaProblems,report.content_audit.structured_data_problem_pages],
   ] as [string,number][]).map(([label,count])=><div key={label}><dt>{label}</dt><dd>{count}</dd></div>)}</dl><p>{c.auditNext}</p><details><summary>{c.limitations}</summary><ul>{report.content_audit.limitations.map(note=><li key={note}>{note}</li>)}</ul></details></section>}
   <section className="panel"><h2>{c.baseline}</h2><ol lang="de">{report.queries.map(q=><li key={q.id}>{q.query}</li>)}</ol></section>
   <section className="panel"><h2>{c.observations}</h2>{!report.observations.length&&<p>{c.noObservations}</p>}{report.observations.map(o=><article className="asset" key={o.id}><h3>{o.platform} · {o.model}</h3><p lang="de">{o.query}</p><p>{o.evidence_type} · {o.search_mode} · {new Date(o.observed_at).toLocaleString('en-GB')}</p><p>{c.brand}: {o.brand_mentioned?c.true:c.false}</p><p>{c.provenance}: {o.provenance}</p><details><summary>{c.originals}</summary><blockquote lang="de">{o.excerpt}</blockquote><ul>{o.cited_urls.map(url=><li key={url}><a href={url} target="_blank" rel="noreferrer">{url}</a></li>)}</ul></details></article>)}</section>
   <section className="panel"><h2>{c.import}</h2><p>{c.importHelp}</p><label>{c.import}<textarea rows={8} maxLength={14000} value={input} onChange={e=>setInput(e.target.value)} placeholder={'{"platform":"claude","query":"…","model":"…","search_mode":"web_search","evidence_type":"consumer_interface","observed_at":"2026-10-20T10:00:00Z","excerpt":"…","cited_urls":[],"provenance":"Manual observation"}'}/></label><button disabled={active||!input.trim()} onClick={save}>{c.save}</button></section>
  </>}
 </main>;
}
