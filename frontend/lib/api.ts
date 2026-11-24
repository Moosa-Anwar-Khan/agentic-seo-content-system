export type FailureSummary={reference:string;component:string;operation:string;category:string;reason:string;saved_work:string;next_action:string;retry:string;code:string;job_id?:string|null;revision?:number|null;unit_id?:string|null;asset_id?:string|null;occurred_at?:string};
export type UserError = {code:string;title:string;message:string;next_steps:string[];retry:string;reference?:string;failure?:FailureSummary;diagnostic_report?:unknown};
export const localErrors:Record<string,UserError>={
 connection:{failure:{reference:'browser-connection',component:'Portal connection',operation:'Contact local server',category:'connection_interruption',reason:'The browser could not reach the server; server task outcome is unknown.',saved_work:'Previously saved work remains on the server. A submitted task may still be running.',next_action:'Reconnect and inspect saved task status before repeating any paid action.',retry:'inspect_first',code:'connection'},code:'connection',title:'Connection interrupted',message:'The portal cannot reach the local backend. A submitted task may still be running.',next_steps:['Check that the backend is running. Run python run.py check for local diagnostics, then reconnect and inspect saved tasks before submitting again.'],retry:'inspect_first'},
 session:{code:'session',title:'Please sign in again',message:'Your session has expired or sign-in is required.',next_steps:['Sign in and reopen your saved run. Previously saved work remains available.'],retry:'sign_in'},
 permission:{code:'permission',title:'This action is not available',message:'Your account or security check does not allow this action.',next_steps:['Refresh the page. Ask the administrator to check your role if it remains blocked.'],retry:'inspect_first'},
 validation:{code:'validation',title:'Check your input',message:'A required field or file does not meet the requirements.',next_steps:['Check required fields, file format and size.'],retry:'correct_input'},
 conflict:{code:'conflict',title:'This step is not ready yet',message:'The revision changed, another task is active, or an approval is missing.',next_steps:['Refresh the saved run and review the requirements for this stage.'],retry:'inspect_first'},
 processing:{code:'processing',title:'The action could not be completed',message:'Previously saved work remains available. The latest operation needs inspection.',next_steps:['Check the saved task status before retrying. Contact the administrator if the problem persists.'],retry:'inspect_first'},
};
export class PortalError extends Error {
 constructor(public info:UserError,public status=0){super(info.message);}
}
export function errorInfo(error:unknown):UserError {return error instanceof PortalError?error.info:localErrors.processing;}
export async function portalFetch(path:string,init:RequestInit={},csrf?:string):Promise<Response>{
 const cookie=typeof document!=='undefined'?document.cookie.split('; ').find(c=>c.startsWith('review_csrf='))?.slice('review_csrf='.length):'';
 let response:Response;
 try {response=await fetch('/api'+path,{...init,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json',...((csrf||cookie)&&init.method==='POST'?{'X-CSRF-Token':csrf||decodeURIComponent(cookie||'')}:{}),...init.headers}});}
 catch {throw new PortalError(localErrors.connection);}
 if(!response.ok){
  const payload=await response.json().catch(()=>({}));
  const supplied=payload.user_error;
  const info=supplied&&typeof supplied.code==='string'&&typeof supplied.title==='string'&&typeof supplied.message==='string'&&Array.isArray(supplied.next_steps)?supplied:
    localErrors[response.status===401?'session':response.status===403?'permission':response.status===422||response.status===413?'validation':response.status===409?'conflict':response.status>=502?'connection':'processing'];
  throw new PortalError(info,response.status);
 }
 if(['/auth/setup/confirm','/auth/verify','/session/logout'].includes(path)&&typeof window!=='undefined'){
  const identity=path==='/session/logout'?'':(await response.clone().json()).user_id;
  window.dispatchEvent(new CustomEvent('portal-own-session',{detail:identity}));
 }
 return response;
}
