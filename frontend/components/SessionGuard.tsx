'use client';
import { useEffect, useRef, useState } from 'react';

// Remount private views when another tab logs out or changes account. Requests and
// Next navigation use no-store; no content is written to shared browser storage.
export default function SessionGuard({children}:{children:React.ReactNode}) {
 const identity=useRef<string|undefined>(undefined),generation=useRef(0);
 const [version,setVersion]=useState(0);
 useEffect(()=>{
  let active=true,pending=false;
  function ownChange(event:Event){identity.current=(event as CustomEvent<string>).detail;generation.current++;}
  async function check(){
   if(pending||document.visibilityState==='hidden')return;
   pending=true;const started=generation.current;
   try{
    const response=await fetch('/api/session',{cache:'no-store',credentials:'same-origin'});
    if(!active||started!==generation.current)return;
    if(response.status!==401&&!response.ok)return;
    const next=response.ok?(await response.json()).user_id:'';
    if(!active||started!==generation.current)return;
    if(identity.current!==undefined&&identity.current!==next)setVersion(v=>v+1);
    identity.current=next;
   }catch{/* An interruption retains saved views; activation still applies on the API. */}
   finally{pending=false;}
  }
  window.addEventListener('portal-own-session',ownChange);
  window.addEventListener('focus',check);document.addEventListener('visibilitychange',check);
  const timer=setInterval(check,4000);void check();
  return()=>{active=false;clearInterval(timer);window.removeEventListener('portal-own-session',ownChange);window.removeEventListener('focus',check);document.removeEventListener('visibilitychange',check);};
 },[]);
 return <div key={version}>{children}</div>;
}
