'use client';
import { useRef, useState } from 'react';
import { portalFetch, errorInfo, localErrors, type UserError } from '../lib/api';
import { accountCopy as c } from '../lib/accounts';
import ErrorNotice from './ErrorNotice';

type Challenge = {challenge:string;stage:string};
export default function RegistrationForm({bootstrap,onBack,onSetup}:{bootstrap:boolean;onBack:()=>void;onSetup:(result:Challenge)=>Promise<void>}) {
 const [name,setName]=useState(''),[email,setEmail]=useState(''),[password,setPassword]=useState(''),[confirmation,setConfirmation]=useState(''),[code,setCode]=useState('');
 const pending=useRef(false);
 const [busy,setBusy]=useState(false),[done,setDone]=useState(false),[error,setError]=useState<UserError|null>(null);
 async function submit(e:React.FormEvent<HTMLFormElement>){
  e.preventDefault();if(pending.current)return;
  if(password!==confirmation||password.length<12||password.length>128){setError({...localErrors.validation,message:c.mismatch});return;}
  pending.current=true;setBusy(true);setError(null);
  try{
   const result=await(await portalFetch(bootstrap?'/auth/bootstrap':'/auth/register',{method:'POST',body:JSON.stringify({name,email,password,password_confirmation:confirmation,...(bootstrap?{setup_code:code}:{})})})).json();
   setPassword('');setConfirmation('');setCode('');
   if(bootstrap)await onSetup(result);else setDone(true);
  }catch(e){setError(errorInfo(e));}finally{pending.current=false;setBusy(false);}
 }
 return <div className="registration-form"><h2>{done?c.pendingTitle:bootstrap?c.setup:c.create}</h2><p className="hint" role={done?'status':undefined}>{done?c.pending:bootstrap?c.setupHelp:c.registrationHelp}</p>
  <ErrorNotice info={error}/>
  {!done&&<form onSubmit={submit}><fieldset disabled={busy}>
   <label>{c.name}<input required autoComplete="name" maxLength={100} value={name} onChange={e=>setName(e.target.value)}/></label>
   <label>{c.email}<input required type="email" autoComplete="username" maxLength={254} value={email} onChange={e=>setEmail(e.target.value)}/></label>
   <label>{c.password}<input required type="password" autoComplete="new-password" minLength={12} maxLength={128} value={password} onChange={e=>setPassword(e.target.value)}/></label>
   <label>{c.confirm}<input required type="password" autoComplete="new-password" minLength={12} maxLength={128} value={confirmation} onChange={e=>setConfirmation(e.target.value)}/></label>
   {bootstrap&&<label>{c.code}<input required type="password" autoComplete="off" value={code} onChange={e=>setCode(e.target.value)}/></label>}
   <button type="submit" disabled={busy}>{busy?c.submitting:bootstrap?c.setup:c.create}</button>
  </fieldset></form>}
  <button className="secondary" disabled={busy} onClick={onBack}>{c.back}</button>
 </div>;
}
