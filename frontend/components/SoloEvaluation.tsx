'use client';
import { useEffect, useRef, useState } from 'react';
import { portalFetch, errorInfo } from '../lib/api';
import EvaluationImages from './EvaluationImages';
import TestWalkthrough from './TestWalkthrough';
type Finding={kind:'yellow'|'red'|'blue';section:string;message_en:string;action_en:string;quote?:string;evidence_quote?:string;evidence_ref?:string;reference?:string};
type Unit={id:string;provider:string;model:string;reservation_microusd:number;max_input_tokens:number;max_output_tokens:number};
type Estimate={label:string;revision:number;plan_id:string;ready:boolean;units:Unit[];reused:string[];maximum_reservation_microusd:number;remaining_microusd:number;maximum_tokens:number;remaining_tokens:number;article_qa:{reused:{id:string}[];pending:{id:string}[]};findings:Finding[];tools:{tool:string;configured:boolean;enabled:boolean;live_status:string;last_verified_at?:string|null;evaluation_use:string;reason:string}[];disclosure:string;limitations:string[]};
type Evaluation={status:string;revision:number;findings:Finding[];results:Record<string,unknown>;release_requirements?:string[]};
type AssistantReview={label:string;revision:number;method:string;reviewed_at:string;notice:string;observations:{category:string;message_en:string}[];limitations:string[]};
const usd=(n:number)=>'$'+(n/1000000).toFixed(4);
export default function SoloEvaluation({runId,revision,allowed,busy,onSubmitted}:{runId:string;revision:number;allowed:boolean;busy:boolean;onSubmitted:()=>Promise<void>}){
 const [open,setOpen]=useState(false),[estimate,setEstimate]=useState<Estimate|null>(null),[result,setResult]=useState<Evaluation|null>(null),[working,setWorking]=useState(false),[error,setError]=useState(''),[confirmed,setConfirmed]=useState(false),[job,setJob]=useState(''),[preview,setPreview]=useState(false),[imageFailed,setImageFailed]=useState(false);
 const submitting=useRef(false),key=useRef<string|null>(null),generation=useRef(0);
 const [assistantReview,setAssistantReview]=useState<AssistantReview|null>(null);
 useEffect(()=>{generation.current++;setOpen(false);setEstimate(null);setResult(null);setAssistantReview(null);setError('');setConfirmed(false);setJob('');key.current=null;},[runId,revision]);
 async function load(){const g=generation.current;setWorking(true);setError('');try{
   const optional=portalFetch('/runs/'+runId+'/evaluation/assistant-review').then(r=>r.json()).catch(()=>undefined);
   const [p,r,a]=await Promise.all([portalFetch('/runs/'+runId+'/evaluation/preflight'),portalFetch('/runs/'+runId+'/evaluation'),optional]);
   const [pData,rData]=await Promise.all([p.json(),r.json()]);if(g!==generation.current)return;
   setEstimate(pData);setResult(rData.evaluation);setConfirmed(false);setJob('');key.current=null;
   if(a!==undefined)setAssistantReview(a.review);
 }catch(e){if(g===generation.current)setError(errorInfo(e).message);}finally{if(g===generation.current)setWorking(false);}}
 async function start(){if(submitting.current||!estimate||!confirmed)return;submitting.current=true;setWorking(true);setError('');const g=generation.current;
   try{key.current=key.current||crypto.randomUUID();const response=await portalFetch('/jobs',{method:'POST',body:JSON.stringify({kind:'evaluation',run_id:runId,expected_revision:revision,idempotency_key:key.current,evaluation_plan_id:estimate.plan_id,confirm_evaluation:true})});const data=await response.json();if(g!==generation.current)return;setJob(data.id);setConfirmed(false);await onSubmitted();}
   catch(e){if(g===generation.current)setError(errorInfo(e).message+' Refresh saved results before submitting again.');}
   finally{submitting.current=false;if(g===generation.current)setWorking(false);}}
 async function download(){setWorking(true);try{const r=await portalFetch('/runs/'+runId+'/evaluation/download');const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download=r.headers.get('content-disposition')?.match(/filename="([a-zA-Z0-9._-]+)"/)?.[1]||'evaluation-unapproved-r'+revision+'.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}catch(e){setError(errorInfo(e).message);}finally{setWorking(false);}}
 const findings=result?.findings||estimate?.findings||[];
 const social=result?.results.social_generate as {linkedin?:string;google_business_profile?:string}|undefined;
 return <section className="panel" id="solo-evaluation"><h2>Solo evaluation</h2><p>Inspect the outputs yourself, without a tax-adviser account. <strong>AI-assisted evaluation — unapproved.</strong></p><p>Your normal workflow, passed checks and draft stay saved. Professional attestation and final approval remain pending. Nothing publishes.</p>
 <TestWalkthrough runId={runId} revision={revision} allowed={allowed} busy={busy}/>
 <button disabled={!allowed} onClick={()=>window.open('/api/runs/'+runId+'/evaluation/review-package','_blank','noopener,noreferrer')}>Open review package — Free</button><p>View the current article and available outputs together. Previous-revision examples are labelled separately. Nothing is submitted or published.</p>
 {!open?<><button className="secondary" disabled={busy||!allowed} onClick={()=>{setOpen(true);void load();}}>Evaluate outputs — Free to open</button>{!allowed&&<p>The owner, administrator or authorized final reviewer can open evaluation.</p>}</>:<>
 <p aria-label="Evaluation steps">1 Review estimate → 2 Evaluate → 3 Inspect outputs → 4 Download evaluation package</p>
 {error&&<p role="alert">{error} Loaded results remain available.</p>}
 <button className="secondary" disabled={working} onClick={()=>void load()}>Refresh evaluation results · Free</button>
 {estimate&&<><h3>Review estimate</h3><p>Article QA: {estimate.article_qa.reused.length} compatible checks reused; {estimate.article_qa.pending.length} pending. Evaluation checks reused: {estimate.reused.length}.</p>
 <p>Maximum reservation: <strong>{usd(estimate.maximum_reservation_microusd)}</strong>. Remaining allowance: {usd(estimate.remaining_microusd)}. Token reservation: {estimate.maximum_tokens.toLocaleString()}; remaining: {estimate.remaining_tokens.toLocaleString()}.</p>
 <ul>{estimate.units.map(u=><li key={u.id}>{u.id.replaceAll('_',' ')} · {u.provider}/{u.model} · up to {usd(u.reservation_microusd)}</li>)}</ul>
 <p>{estimate.disclosure}</p>
 {job?<p role="status">Evaluation job saved. Watch task progress, then refresh evaluation results. No duplicate submission is needed. Reference: {job}</p>:<><label><input type="checkbox" disabled={!estimate.ready||working} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>I confirm only this displayed plan, its maximum reservation and private provider processing.</label><button disabled={busy||working||!estimate.ready||!confirmed} onClick={()=>void start()}>{estimate.maximum_reservation_microusd?'Run confirmed evaluation · May incur a charge':'Save evaluation snapshot · Free'}</button>{!estimate.ready&&<p>Evaluation is blocked by an active task, an unresolved request or insufficient allowance. Read the findings below. You can still download partial unapproved outputs.</p>}</>}
 <details><summary>Tool use and limitations</summary>{estimate.tools.map(t=><p key={t.tool}><strong>{t.tool}</strong>: {t.evaluation_use}. Enabled: {t.enabled?'yes':'no'}; configured: {t.configured?'yes':'no'}; status: {t.live_status}; last verification: {t.last_verified_at||'not verified'}. {t.reason}</p>)}<ul>{estimate.limitations.map(x=><li key={x}>{x}</li>)}</ul></details></>}
 <h3>Inspect outputs</h3><p>{result?'Saved evaluation: '+result.status.replaceAll('_',' ')+' · Revision '+result.revision:'No completed evaluation yet. The current article and infographic remain available in the normal draft preview and partial download.'}</p>
 {assistantReview&&<article aria-label="Independent image review"><h3>Independent image review</h3><p><strong>{assistantReview.label} · Revision {assistantReview.revision}</strong></p><p>{assistantReview.notice}</p><ul>{assistantReview.observations.map((o,i)=><li key={i}>{o.category==='suggestion'?'Optional suggestion: ':''}{o.message_en}</li>)}</ul><button type="button" onClick={()=>window.open('/api/runs/'+runId+'/evaluation/combined-review','_blank','noopener,noreferrer')}>Open all outputs and review · Free</button><p>Opens one page with the article, infographic and both saved social previews. Nothing is submitted or published.</p><details><summary>Review method and limitations</summary><p>{assistantReview.method}</p><p>Recorded: {new Date(assistantReview.reviewed_at).toLocaleString('en-GB')}</p><ul>{assistantReview.limitations.map((x,i)=><li key={i}>{x}</li>)}</ul></details></article>}
 <button className="secondary" onClick={()=>setPreview(!preview)}>{preview?'Hide article and infographic':'View German article and infographic · Free'}</button>
 {preview&&<><p><strong>AI-assisted evaluation — unapproved · Revision {revision}</strong></p><iframe title="Evaluation article — unapproved" lang="de" sandbox="" src={'/api/runs/'+runId+'/review-preview?revision='+revision}/>{!imageFailed?<figure><img className="infographic-preview" src={'/api/runs/'+runId+'/evaluation/infographic'} alt="Current saved German infographic — unapproved" onError={()=>setImageFailed(true)}/><figcaption>Saved infographic · revision {revision} · unapproved</figcaption></figure>:<p>Image preview unavailable. The draft stays visible; inspect the evaluation download or the saved attachment.</p>}</>}
 {findings.map((f,i)=><article className={'qa-finding qa-'+(f.kind==='red'?'correction':f.kind==='yellow'?'suggestion':'review_error')} key={i}><strong>{f.kind==='red'?'🔴 Correction required':f.kind==='yellow'?'🟡 Optional suggestion':'🔵 Review incomplete'} · {f.section}</strong><p>{f.message_en}</p>{f.quote&&<blockquote lang="de">{f.quote}</blockquote>}<p>{f.action_en}</p>{f.evidence_quote&&<details><summary>Supporting evidence: {f.evidence_ref}</summary><blockquote>{f.evidence_quote}</blockquote></details>}{f.reference&&<p>Debug reference: {f.reference}</p>}</article>)}
 {social&&<><h4>LinkedIn preview — unapproved</h4><p lang="de" style={{whiteSpace:'pre-wrap'}}>{social.linkedin}</p><h4>Google Business Profile preview — unapproved</h4><p lang="de" style={{whiteSpace:'pre-wrap'}}>{social.google_business_profile}</p></>}
 <EvaluationImages runId={runId} revision={revision} busy={busy} onSubmitted={onSubmitted}/>
 <details><summary>Requirements before release</summary><p>Qualified professional attestation and trusted final approval remain required. Customer reviews are optional.</p><ul>{result?.release_requirements?.map((x,i)=><li key={i}>{x}</li>)}</ul></details>
 <h3>Final test step: export</h3><p>Download the saved article, infographic, social previews, evidence and any generated channel images. This does not require professional approval; it stays unapproved.</p>
 <button disabled={working} onClick={()=>void download()}>Download evaluation package — unapproved · Free</button><p>Partial outputs can be downloaded even when checks or release inputs remain unresolved.</p>
 </>}
 </section>;
}
