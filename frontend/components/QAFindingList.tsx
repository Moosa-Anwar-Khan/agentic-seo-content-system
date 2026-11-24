'use client';
export type PortalFinding={finding_id?:string;run_id?:string;display_kind?:string;code:string;category:string;blocking:boolean;message:string;action:string;quote?:string;original_finding?:string;module_id?:string;reviewed_revision?:number;reference?:string;metadata_field?:string;material_impact?:string;requirement_id?:string;conflicting_passages?:{module_id:string;quote:string}[];location?:{status:string;module_id?:string;field?:string;explanation?:string|null};evidence_refs?:string[];source_quotes?:{evidence_ref:string;quote:string}[]};
const labels:Record<string,string>={suggestion:'Suggestion',correction:'Correction required',review_error:'QA review error',information:'Information',release:'Requirement before release'};
export default function QAFindingList({findings,onShow}:{findings:PortalFinding[];onShow?:(finding:PortalFinding)=>void}) {
 return <div className="qa-findings">{['review_error','correction','suggestion','release','information'].map(kind=>{
 const items=findings.filter(item=>(item.display_kind||'information')===kind);if(!items.length)return null;
 return <section key={kind} aria-label={labels[kind]}><h3>{labels[kind]} ({items.length})</h3>{items.map((item,index)=><article key={item.finding_id||item.code+index} className={'qa-finding qa-'+kind}>
  <strong><span aria-hidden="true">{kind==='correction'?'!':kind==='suggestion'?'△':'ⓘ'}</span> {labels[kind]}</strong><p>{item.message}</p>
  {item.quote&&kind!=='review_error'&&<blockquote lang="de">{item.quote}</blockquote>}<p>{item.action}</p>
  {item.material_impact&&<p><strong>Why it matters:</strong> {item.material_impact}</p>}
  {!!item.conflicting_passages?.length&&<details><summary>Compare the conflicting passages</summary>{item.conflicting_passages.map((passage,i)=><blockquote key={i} lang="de">{passage.quote}<small>Section {passage.module_id}</small></blockquote>)}</details>}
  <p className="hint">{item.module_id?`Section ${item.module_id} · `:''}Revision {item.reviewed_revision??'not recorded'}{kind==='suggestion'?' · Optional; does not block progression':kind==='review_error'?' · Draft unchanged; valid QA still required':''}</p>
  {item.location?.explanation&&<p>{item.location.explanation}</p>}
  {onShow&&(item.module_id||item.location?.field)&&<button className="secondary" onClick={()=>onShow(item)}>Show in draft · Free</button>}
  {!!item.evidence_refs?.length&&<details><summary>Evidence and source proof</summary><p>{item.evidence_refs.join(', ')}</p>{item.source_quotes?.map((q,i)=><blockquote key={i} lang="de">{q.quote}<small>{q.evidence_ref}</small></blockquote>)}</details>}
  {item.original_finding&&!item.quote&&<details><summary>Original German finding</summary><p lang="de">{item.original_finding}</p></details>}
  {item.reference&&<small>Reference: {item.reference}</small>}
 </article>)}</section>;})}</div>;
}
