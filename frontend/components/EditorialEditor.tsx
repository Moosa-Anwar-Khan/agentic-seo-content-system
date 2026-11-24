'use client';
import { useEffect, useRef, useState } from 'react';
import { t } from '../lib/ui';

export type EditableEditorial = Record<string,unknown> & {
 modules:{id:string;markdown:string}[];
 claims?:{id:string;text:string;source_id:string;quote:string;tax_year:number;conditions:string}[];
};

export type FeeMode = 'numeric_prices'|'approved_wording_only';
export default function EditorialEditor({document,busy,onSave,feeMode='numeric_prices'}:{document:EditableEditorial;busy:boolean;feeMode?:FeeMode;
 onSave:(document:EditableEditorial,notes:string,editId:string,feePolicy?:{mode:FeeMode;confirmed:true})=>Promise<void>}) {
 const [draft,setDraft]=useState<EditableEditorial>(()=>structuredClone(document));
 const [notes,setNotes]=useState(t('local_edit_default_note'));
 const [selectedFee,setSelectedFee]=useState<FeeMode>(feeMode),[feeConfirmed,setFeeConfirmed]=useState(false);
 const editId=useRef(crypto.randomUUID());
 const changed=JSON.stringify(draft)!==JSON.stringify(document)||selectedFee!==feeMode;
 const feeReady=selectedFee===feeMode||feeConfirmed;
 useEffect(()=>{window.dispatchEvent(new CustomEvent('muster-unsaved-edit',{detail:changed}));return()=>{window.dispatchEvent(new CustomEvent('muster-unsaved-edit',{detail:false}));};},[changed]);
 useEffect(()=>{if(!changed)return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[changed]);
 return <details className="panel"><summary>{t('edit_saved_german_draft')}</summary>
  <p>{t('local_edit_help')}</p>
  <p role="status">{changed?'Unsaved changes — save explicitly before leaving.':'No unsaved changes.'}</p><p className="hint">Saving creates a revision and uses one revision allowance. Dependent QA, infographic binding and approvals must be renewed. Typing does not create revisions or call AI.</p>
  <form onSubmit={event=>{event.preventDefault();if(changed&&feeReady&&!busy)void onSave(draft,notes,editId.current,selectedFee!==feeMode?{mode:selectedFee,confirmed:true}:undefined);}}>
   <fieldset disabled={busy}>
    <legend>{t('german_article_sections')}</legend>
    {draft.modules.map((module,index)=><details key={module.id}><summary>{t('section_label')} {module.id}</summary>
     <label>{t('german_markdown')} {module.id}<textarea aria-label={`${t('german_markdown')} ${module.id}`} lang="de" rows={12} value={module.markdown}
      onChange={event=>setDraft(current=>({...current,modules:current.modules.map((item,i)=>i===index?{...item,markdown:event.target.value}:item)}))}/></label>
    </details>)}
    <h3>{t('claim_source_sheet')}</h3>
    {(draft.claims||[]).map((claim,index)=><details key={claim.id}><summary>{claim.id}</summary>
     <p>{t('source_reference')} {claim.source_id} · {claim.tax_year}</p>
     <label>{t('exact_article_claim')} {claim.id}<textarea aria-label={`${t('exact_article_claim')} ${claim.id}`} lang="de" rows={3} value={claim.text}
      onChange={event=>setDraft(current=>({...current,claims:current.claims?.map((item,i)=>i===index?{...item,text:event.target.value}:item)}))}/></label>
     <label>{t('exact_source_quote')} {claim.id}<textarea aria-label={`${t('exact_source_quote')} ${claim.id}`} lang="de" rows={4} value={claim.quote}
      onChange={event=>setDraft(current=>({...current,claims:current.claims?.map((item,i)=>i===index?{...item,quote:event.target.value}:item)}))}/></label>
    </details>)}
    {document.page_type==='landing_page'&&<><label>Fee presentation for this page<select value={selectedFee} onChange={event=>{setSelectedFee(event.target.value as FeeMode);setFeeConfirmed(false);}}><option value="numeric_prices">Approved numeric prices required</option><option value="approved_wording_only">Approved fee wording only</option></select></label><p className="hint">This affects only this page. It does not supply prices, change the article text or approve release.</p>{selectedFee!==feeMode&&<label><input type="checkbox" checked={feeConfirmed} onChange={event=>setFeeConfirmed(event.target.checked)}/>I explicitly approve this fee-presentation change for this page.</label>}</>}
    <label>{t('edit_note')}<textarea rows={3} value={notes} maxLength={2000} onChange={event=>setNotes(event.target.value)}/></label>
    <button disabled={busy||!changed||!feeReady||notes.trim().length<10} type="submit">Save changes — Free</button>
   </fieldset>
  </form>
 </details>;
}
