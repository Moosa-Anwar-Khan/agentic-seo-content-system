'use client';
import {useEffect,useRef,useState} from 'react';
import {portalFetch,errorInfo} from '../lib/api';
type Plan={plan_id:string;revision:number;ready:boolean;reason:string|null;provider:string;model:string;quality:string;maximum_reservation_microusd:number;remaining_microusd:number;disclosure:string;images:{images:Record<string,{filename:string;dimensions:number[]}>}|null};
const labels:Record<string,string>={website:'WordPress',linkedin:'LinkedIn',google_business_profile:'Google Business Profile'};
export default function EvaluationImages({runId,revision,busy,onSubmitted}:{runId:string;revision:number;busy:boolean;onSubmitted:()=>Promise<void>}){
 const [plan,setPlan]=useState<Plan|null>(null),[confirmed,setConfirmed]=useState(false),[working,setWorking]=useState(false),[error,setError]=useState(''),[job,setJob]=useState('');
 const generation=useRef(0),pending=useRef(false),id=useRef<string|null>(null);
 async function load(){const g=generation.current;setWorking(true);try{const r=await portalFetch('/runs/'+runId+'/evaluation/images/preflight');const value=await r.json();if(g===generation.current){setPlan(value);setConfirmed(false);setError('');}}catch(e){if(g===generation.current)setError(errorInfo(e).message);}finally{if(g===generation.current)setWorking(false);}}
 useEffect(()=>{generation.current++;setPlan(null);setConfirmed(false);setJob('');id.current=null;void load();return()=>{generation.current++;};},[runId,revision]);
 async function generate(){if(pending.current||!plan?.ready||!confirmed)return;pending.current=true;setWorking(true);const g=generation.current;try{
  id.current=id.current||crypto.randomUUID();const r=await portalFetch('/jobs',{method:'POST',body:JSON.stringify({kind:'evaluation_images',run_id:runId,expected_revision:revision,idempotency_key:id.current,evaluation_plan_id:plan.plan_id,confirm_evaluation:true})});const value=await r.json();if(g===generation.current){setJob(value.id);setConfirmed(false);await onSubmitted();}
 }catch(e){if(g===generation.current)setError(errorInfo(e).message+' Refresh saved images before trying anything else.');}finally{pending.current=false;if(g===generation.current)setWorking(false);}}
 return <section aria-label="Test images and export"><h3>Platform images for your test export</h3>
 <p>Generate one master image, then create WordPress, LinkedIn and Google Business Profile sizes locally. These are separate from your article infographic. Nothing publishes.</p>
 {error&&<p role="alert">{error} Saved outputs remain available.</p>}
 <button className="secondary" disabled={working} onClick={()=>void load()}>Refresh saved images · Free</button>
 {plan?.images?<><p role="status"><strong>Three test images saved — unapproved.</strong> Inspect them, then download your evaluation package below. It now includes the channel-images folder.</p>
 {Object.entries(plan.images.images).map(([platform,item])=><figure key={platform} style={{margin:'16px 0'}}><img className="infographic-preview" src={'/api/runs/'+runId+'/evaluation/images/'+platform} alt={labels[platform]+' generated evaluation image — unapproved'}/><figcaption>{labels[platform]} · {item.dimensions.join(' × ')} · {item.filename} · visual approval pending</figcaption></figure>)}</>:
 plan&&<><p>{plan.provider}/{plan.model} · {plan.quality} · one request. Maximum reservation: <strong>${(plan.maximum_reservation_microusd/1e6).toFixed(4)}</strong>. Remaining allowance: ${(plan.remaining_microusd/1e6).toFixed(4)}.</p><p>{plan.disclosure}</p>
 {job?<p role="status">Image task saved. Follow task progress, then click Refresh saved images. No repeat submission is needed. Reference: {job}</p>:<><label><input type="checkbox" checked={confirmed} disabled={!plan.ready||working||busy} onChange={e=>setConfirmed(e.target.checked)}/>I confirm this one image request and its displayed reservation. Outputs stay unapproved.</label><button disabled={!plan.ready||!confirmed||working||busy} onClick={()=>void generate()}>Generate platform images · May incur a charge</button></>}
 {plan.reason&&<p>{plan.reason}</p>}</>}
 </section>;
}
