'use client';
import { useEffect, useState } from 'react';

export default function InfographicUpload({revision,saved,html,alt,disabledReason,busy,onUpload}:{
 revision:number;saved:boolean;html:string;alt:string;disabledReason:string|null;
 busy:boolean;onUpload:(file:File)=>Promise<void>;
}) {
 const [preview,setPreview]=useState('');
 const [uploading,setUploading]=useState(false);
 useEffect(()=>{
  setPreview('');
  if(!saved)return;
  // Template content is inert. Accept only the embedded WebP saved by the server;
  // never load an arbitrary URL from article markup into the portal.
  const template=document.createElement('template');template.innerHTML=html;
  const source=template.content.querySelector('.graphic-placeholder img')?.getAttribute('src')||'';
  if(/^data:image\/webp;base64,[A-Za-z0-9+/]+=*$/.test(source))setPreview(source);
 },[saved,html,revision]);
 async function choose(file:File) {
  setUploading(true);
  try {await onUpload(file);} finally {setUploading(false);}
 }
 return <div className="infographic-upload" aria-label="Infographic upload">
  {saved?<div className="infographic-saved" role="status">
   <strong>✓ Infographic saved for revision {revision}.</strong>
   <p>You do not need to upload it again. It is attached to this draft. Uploading it here does not publish it.</p>
  </div>:<p>Choose your WebP image below. It will be saved automatically in this run, without publishing it.</p>}
  {preview&&<figure><img className="infographic-preview" src={preview} alt={alt}/><figcaption>Saved infographic · revision {revision}</figcaption></figure>}
  {saved&&!preview&&<p className="hint">The image is saved, but its preview is unavailable. Open the saved draft to inspect it; do not upload it again just to refresh the preview.</p>}
  <label htmlFor="infographic-file">{saved?'Replace infographic (optional)':'Choose infographic image'} · Free</label>
  <input id="infographic-file" type="file" accept="image/webp" aria-describedby="infographic-file-help" disabled={busy||uploading||!!disabledReason}
   onChange={event=>{const file=event.currentTarget.files?.[0];event.currentTarget.value='';if(file)void choose(file);}}/>
  <p id="infographic-file-help" className="hint">WebP · 1200–1600 pixels wide · at most 3200 pixels high · under 300 KB. {saved?'Replacing the saved image requires a new professional review.':''}</p>
  {disabledReason&&<p className="hint">{disabledReason}</p>}
  {uploading&&<p role="status">Uploading and saving your infographic…</p>}
 </div>;
}
