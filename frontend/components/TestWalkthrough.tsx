'use client';
import { useEffect, useRef, useState } from 'react';
import { errorInfo, portalFetch } from '../lib/api';

type Step = {number:number;label:string;description:string;mode:string;available:boolean};
type Rehearsal = {label:string;revision:number;context:string;ready:boolean;blockers:string[];steps:Step[];notice:string};

export default function TestWalkthrough({runId,revision,allowed,busy}:{runId:string;revision:number;allowed:boolean;busy:boolean}) {
 const [data,setData]=useState<Rehearsal|null>(null),[step,setStep]=useState(0),[working,setWorking]=useState(false),[error,setError]=useState('');
 const generation=useRef(0),heading=useRef<HTMLHeadingElement>(null),pending=useRef(false);
 useEffect(()=>{generation.current++;setData(null);setStep(0);setError('');setWorking(false);pending.current=false;return()=>{generation.current++;};},[runId,revision,allowed]);
 async function open(){
  if(pending.current)return;pending.current=true;const g=generation.current;setWorking(true);setError('');
  try {const r=await portalFetch('/runs/'+runId+'/evaluation/test-walkthrough');const next:Rehearsal=await r.json();if(g!==generation.current)return;
   if(next.revision!==revision){setError('The revision changed. Refresh saved status before opening the test.');return;}
   setData(next);setStep(0);requestAnimationFrame(()=>heading.current?.focus());
  }catch(e){if(g===generation.current)setError(errorInfo(e).message);}
  finally{if(g===generation.current){setWorking(false);pending.current=false;}}
 }
 async function download(){
  if(!data||!data.ready||pending.current)return;pending.current=true;const g=generation.current;setWorking(true);setError('');
  try {
   const r=await portalFetch('/runs/'+runId+'/evaluation/test-export?revision='+revision+'&context='+data.context);
   const blob=await r.blob();if(g!==generation.current)return;
   const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=r.headers.get('content-disposition')?.match(/filename="([a-zA-Z0-9._-]+)"/)?.[1]||'test-unapproved-r'+revision+'.zip';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);setStep(8);
  }catch(e){if(g===generation.current)setError(errorInfo(e).message+' Reopen the test if saved work changed. No paid request was sent.');}
  finally{if(g===generation.current){setWorking(false);pending.current=false;}}
 }
 const current=data?.steps[step];
 return <section className="test-walkthrough" aria-label="Eight-step test walkthrough">
  <h3>Test all eight steps</h3>
  <p>Replay saved work, simulate the two human approvals and download a test export. <strong>Free · No publication · No real approvals.</strong></p>
  {!allowed&&<p>The run owner, administrator or authorized final reviewer can start this test.</p>}
  {busy&&<p role="status">Wait for the current task to finish before continuing the test. Saved work remains available.</p>}
  {!data?<button disabled={!allowed||busy||working} onClick={()=>void open()}>Start eight-step test — Free</button>:<>
   <p><strong>{data.label} · Revision {data.revision}</strong></p>
   <p>{data.notice} Closing or reloading resets this rehearsal only.</p>
   <ol className="test-step-grid" aria-label="Test workflow steps">{data.steps.map((s,i)=><li key={s.number} aria-current={step===i?'step':undefined}>
    <strong>{step>i?'✓ ':''}{s.number}. {s.label}</strong><span>{step>i?(i===5||i===6?'Simulated':'Test step complete'):step===i?'Current test step':'Pending test step'}</span><small>{s.mode}</small>
   </li>)}</ol>
   {!data.ready?<><p role="status">This test needs the saved first five steps to be complete. It does not bypass incomplete QA.</p><ul>{data.blockers.map(b=><li key={b}>{b}</li>)}</ul><button className="secondary" disabled={working} onClick={()=>void open()}>Refresh test prerequisites — Free</button></>:<>
    <h4 ref={heading} tabIndex={-1}>{step===8?'Eight-step test complete':`${step+1}. ${current?.label}`}</h4>
    <p role="status" aria-live="polite">{step===8?'Your unapproved test download was requested. If your browser asks, allow or save it. The normal workflow remains awaiting genuine reviews.':current?.description}</p>
    {step<5&&<p><a href={'/api/runs/'+runId+'/evaluation/review-package'} target="_blank" rel="noopener noreferrer">View saved outputs — Free</a></p>}
    {step===4&&<img className="infographic-preview" src={'/api/runs/'+runId+'/evaluation/infographic'} alt="Current infographic for the test walkthrough — unapproved"/>}
    <div className="test-step-actions">{step<7?<button disabled={busy||working} onClick={()=>{setStep(step+1);requestAnimationFrame(()=>heading.current?.focus());}}>{step===5?'Simulate professional review — Free':step===6?'Simulate final approval — Free':'Continue test — Free'}</button>:<button disabled={busy||working} onClick={()=>void download()}>{step===8?'Download test package again — Free':'Download test export — unapproved · Free'}</button>}</div>
   </>}
   <details><summary>Real release requirements remain pending</summary><p>This rehearsal cannot attest to tax accuracy, approve the article, promote old social/image examples or verify WordPress imports. Use the normal workflow with the required people for an approved release.</p></details>
   <button className="secondary" disabled={working} onClick={()=>{generation.current++;setData(null);setStep(0);setError('');}}>Close test walkthrough</button>
  </>}
  {error&&<p role="alert">{error}</p>}
 </section>;
}
