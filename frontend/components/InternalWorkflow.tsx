'use client';
import { useEffect, useRef, useState } from 'react';
import { portalFetch, errorInfo } from '../lib/api';
type RecordInfo={actor_name:string;recorded_at:string;notes:string};
type Internal={label:string;revision:number;context:string;active:boolean;reviewed:boolean;approved:boolean;exported:boolean;blocking:string[];outstanding_release_requirements:string[];review?:RecordInfo;approval?:RecordInfo;export?:RecordInfo};
export default function InternalWorkflow({runId,revision,allowed,busy,onChanged}:{runId:string;revision:number;allowed:boolean;busy:boolean;onChanged:()=>Promise<void>}){
 const [data,setData]=useState<Internal|null>(null),[working,setWorking]=useState(false),[error,setError]=useState(''),[notes,setNotes]=useState(''),[checks,setChecks]=useState({content:false,sources:false,graphic:false,limits:false,approve:false});
 const generation=useRef(0),pending=useRef(false),submission=useRef<{signature:string;id:string}|null>(null);
 async function load(){const g=generation.current;setWorking(true);setError('');try{const r=await portalFetch('/runs/'+runId+'/internal-workflow');const next:Internal=await r.json();if(g===generation.current){setData(next);setChecks({content:false,sources:false,graphic:false,limits:false,approve:false});}}catch(e){if(g===generation.current)setError(errorInfo(e).message);}finally{if(g===generation.current)setWorking(false);}}
 useEffect(()=>{generation.current++;setData(null);setNotes('');setError('');submission.current=null;pending.current=false;if(allowed)void load();return()=>{generation.current++;};},[runId,revision,allowed]); // Explicit actions only; load never submits work.
 async function act(action:string){if(!data||pending.current)return;pending.current=true;setWorking(true);setError('');const g=generation.current;
  const body={action,expected_revision:revision,context:data.context,notes,content_reviewed:checks.content,sources_reviewed:checks.sources,graphic_reviewed:checks.graphic,limitations_acknowledged:checks.limits,approve_internal_only:checks.approve};
  const signature=JSON.stringify(body);if(submission.current?.signature!==signature)submission.current={signature,id:crypto.randomUUID()};
  try{const r=await portalFetch('/runs/'+runId+'/internal-workflow',{method:'POST',body:JSON.stringify({...body,request_id:submission.current.id})});const next:Internal=await r.json();if(g!==generation.current)return;setData(next);setNotes('');setChecks({content:false,sources:false,graphic:false,limits:false,approve:false});submission.current=null;await onChanged();}
  catch(e){if(g===generation.current)setError(errorInfo(e).message+' Refresh saved internal status before retrying. No paid request is started here.');}
  finally{if(g===generation.current){pending.current=false;setWorking(false);}}
 }
 const ready=!!data&&!data.blocking.length;
 return <section id="guided-internal" className="panel internal-workflow" aria-label="Real internal review and export">
 <h2>Internal review and export</h2><p><strong>Real recorded actions · Free · No publication</strong></p>
 <p>This path records your actual internal review and approval. It does not claim qualified tax-adviser approval or grant permission to publish.</p>
 {!allowed?<p>The run owner or administrator must perform these actions.</p>:<>
 <button className="secondary" disabled={working||busy} onClick={()=>void load()}>Refresh saved internal status — Free</button>
 {data&&<><p><strong>{data.active?data.label:'Current policy: qualified-adviser release workflow'} · Revision {data.revision}</strong></p>
 <p><a href={'/api/runs/'+runId+'/evaluation/review-package'} target="_blank" rel="noopener noreferrer">Open article, infographic and source/output package — Free</a></p>
 {!!data.blocking.length&&<div role="status"><p>Complete these prerequisites first:</p><ul>{data.blocking.map(x=><li key={x}>{x}</li>)}</ul></div>}
 <details open={!data.reviewed}><summary>Unresolved requirements before professional release ({data.outstanding_release_requirements.length})</summary><ul>{data.outstanding_release_requirements.map(x=><li key={x}>{x}</li>)}</ul><p>These remain unresolved and are included in your internal review/export record. Internal approval does not certify them as correct.</p></details>
 {!data.active?<><label><input type="checkbox" checked={checks.limits} onChange={e=>setChecks({...checks,limits:e.target.checked})}/>Use actual owner-led internal review and export for this run, professionally unapproved.</label><button disabled={busy||working||!checks.limits} onClick={()=>void act('enable')}>Use internal-review workflow — Free</button></>:<>
 <ol><li>Internal review: {data.reviewed?'Recorded':'Pending'}</li><li>Final internal approval: {data.approved?'Recorded':'Pending'}</li><li>Internal export: {data.exported?'Created':'Pending'}</li></ol>
 {!data.reviewed?<><h3>6. Review the outputs</h3>
 <label><input type="checkbox" checked={checks.content} onChange={e=>setChecks({...checks,content:e.target.checked})}/>I have inspected the article for internal editorial review.</label>
 <label><input type="checkbox" checked={checks.sources} onChange={e=>setChecks({...checks,sources:e.target.checked})}/>I have inspected the source references and their stated limitations; I am not providing professional tax attestation.</label>
 <label><input type="checkbox" checked={checks.graphic} onChange={e=>setChecks({...checks,graphic:e.target.checked})}/>I have inspected the attached infographic.</label>
 <label><input type="checkbox" checked={checks.limits} onChange={e=>setChecks({...checks,limits:e.target.checked})}/>I acknowledge all outstanding release requirements and the professionally unapproved status.</label>
 <label>Internal review note<textarea maxLength={2000} rows={3} value={notes} onChange={e=>setNotes(e.target.value)}/></label>
 <div className="internal-actions"><button disabled={busy||working||!ready||!checks.content||!checks.sources||!checks.graphic||!checks.limits||notes.trim().length<10} onClick={()=>void act('review')}>Record my internal review — Free</button></div><p>Inspect the saved outputs, confirm each item and enter a note of at least 10 characters. This records your review; it is not a simulation.</p>
 </>:!data.approved?<><h3>7. Final internal approval</h3><p>Internal review recorded by {data.review?.actor_name}. {data.review?.notes}</p>
 <label><input type="checkbox" checked={checks.approve} onChange={e=>setChecks({...checks,approve:e.target.checked})}/>I approve this revision for internal use and internal export only.</label>
 <label><input type="checkbox" checked={checks.limits} onChange={e=>setChecks({...checks,limits:e.target.checked})}/>I understand that tax-adviser approval, outstanding company inputs and publication approval remain separate.</label>
 <div className="internal-actions"><button disabled={busy||working||!ready||!checks.approve||!checks.limits} onClick={()=>void act('approve')}>Record my final internal approval — Free</button></div></>:<><h3>8. Internal export</h3><p>Final internal approval recorded by {data.approval?.actor_name}. This does not authorize publishing.</p>
 {!data.exported?<div className="internal-actions"><button disabled={busy||working||!ready} onClick={()=>void act('export')}>Create internally reviewed export — Free</button></div>:<><p role="status">Internal export created and saved. All eight internal-workflow steps are complete.</p><a className="internal-download" download href={'/api/runs/'+runId+'/internal-workflow/download'}>Download internally reviewed package — professionally unapproved · Free</a></>}
 </>}
 <details><summary>Qualified release workflow and recorded history</summary><p>The qualified-adviser workflow is still available separately. Returning to it does not grant any missing approval. Internal records remain in history.</p><label><input type="checkbox" checked={checks.limits} onChange={e=>setChecks({...checks,limits:e.target.checked})}/>I understand this changes the active workflow, not any professional approval.</label><button className="secondary" disabled={busy||working||!checks.limits} onClick={()=>void act('disable')}>Return to qualified-adviser workflow — Free</button></details>
 </>}
 </>}
 </>}
 {error&&<p role="alert">{error}</p>}
 </section>;
}
