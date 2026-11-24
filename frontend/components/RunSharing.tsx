'use client';
import { useEffect, useState } from 'react';
import { portalFetch, errorInfo, type UserError } from '../lib/api';
import { accountCopy as c } from '../lib/accounts';
import ErrorNotice from './ErrorNotice';

type Person={id:string;name:string;role:string;enabled:number;registration_status:string};
type Sharing={owner_user_id:string|null;shared_user_ids:string[];reviewers:Person[]};
export default function RunSharing({runId,admin,onChange}:{runId:string;admin:boolean;onChange:()=>Promise<void>}){
 const [data,setData]=useState<Sharing|null>(null),[people,setPeople]=useState<Person[]>([]),[target,setTarget]=useState(''),[owner,setOwner]=useState('');
 const [password,setPassword]=useState(''),[code,setCode]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<UserError|null>(null);
 async function refresh(){setData(await(await portalFetch('/runs/'+runId+'/sharing')).json());}
 useEffect(()=>{let active=true;setData(null);setError(null);portalFetch('/runs/'+runId+'/sharing').then(r=>r.json()).then(d=>{if(active)setData(d);}).catch(e=>{if(active)setError(errorInfo(e));});if(admin)portalFetch('/admin/users').then(r=>r.json()).then(d=>{if(active)setPeople(d.filter((u:Person)=>u.enabled&&u.registration_status==='active'));}).catch(e=>{if(active)setError(errorInfo(e));});return()=>{active=false;};},[runId,admin]);
 async function share(id:string,enabled:boolean){if(busy)return;setBusy(true);setError(null);try{await portalFetch('/runs/'+runId+'/sharing',{method:'POST',body:JSON.stringify({user_id:id,enabled})});setTarget('');await refresh();}catch(e){setError(errorInfo(e));}finally{setBusy(false);}}
 async function assign(){if(busy)return;setBusy(true);setError(null);try{await portalFetch('/runs/'+runId+'/owner',{method:'POST',body:JSON.stringify({user_id:owner,admin_password:password,admin_code:code})});setPassword('');setCode('');await refresh();await onChange();}catch(e){setError(errorInfo(e));}finally{setBusy(false);}}
 return <details className="panel"><summary>{c.sharing}</summary><p>{c.sharingHelp}</p><ErrorNotice info={error}/>
  {data&&<>{!data.owner_user_id?<p className="connection-notice">{c.legacy}</p>:<><label>{c.chooseReviewer}<select aria-label={c.chooseReviewer} value={target} onChange={e=>setTarget(e.target.value)}><option value="">{c.selectReviewer}</option>{data.reviewers.filter(u=>u.id!==data.owner_user_id&&!data.shared_user_ids.includes(u.id)).map(u=><option key={u.id} value={u.id}>{u.name} · {u.role==='tax_adviser'?'Professional reviewer':'Final reviewer'}</option>)}</select></label><button disabled={busy||!target} onClick={()=>share(target,true)}>{c.share}</button>{data.shared_user_ids.map(id=><article className="asset" key={id}><strong>{data.reviewers.find(u=>u.id===id)?.name||c.inactiveReviewer}</strong><button className="secondary" disabled={busy} onClick={()=>share(id,false)}>{c.revoke}</button></article>)}</>}
  {admin&&<fieldset disabled={busy}><legend>{c.owner}</legend><p className="hint">{c.ownerHelp}</p><label>{c.activatedOwner}<select aria-label={c.activatedOwner} value={owner} onChange={e=>setOwner(e.target.value)}><option value="">Select owner…</option>{people.map(u=><option key={u.id} value={u.id}>{u.name}</option>)}</select></label><label>{c.adminPassword}<input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)}/></label><label>{c.authenticatorCode}<input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={e=>setCode(e.target.value)}/></label><button disabled={busy||!owner||!password||code.length!==6} onClick={assign}>{c.owner}</button></fieldset>}
  </>}
 </details>;
}
