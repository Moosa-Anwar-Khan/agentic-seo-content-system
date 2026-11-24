'use client';
import GuidedWorkflow, { type StatusChip } from '../components/GuidedWorkflow';
import RunStatusBar from '../components/RunStatusBar';
import SoloEvaluation from '../components/SoloEvaluation';
import InternalWorkflow from '../components/InternalWorkflow';
import InfographicUpload from '../components/InfographicUpload';
import { type PortalFinding } from '../components/QAFindingList';
import RegistrationForm from '../components/RegistrationForm';
import RunSharing from '../components/RunSharing';
import { accountCopy as account } from '../lib/accounts';
import ReviewNote from '../components/ReviewNote';
import EditorialEditor, { type EditableEditorial } from '../components/EditorialEditor';
import RunProgress, { type RunProgressData } from '../components/RunProgress';
import { t } from '../lib/ui';
import { portalFetch, PortalError, errorInfo, localErrors, type UserError } from '../lib/api';
import ErrorNotice from '../components/ErrorNotice';
import { useEffect, useRef, useState } from 'react';
import SEORecommendations, { type Research, type Selection } from './SEORecommendations';
type QualityCheck = {passed:boolean;errors:string[];warnings:string[]};
type Claim = {id:string;text:string;source_id:string;quote:string;tax_year:number;conditions:string};
type Editorial = {claims?:Claim[];modules?:{id:string;markdown:string}[];graphic?:{title:string;notebooklm_prompt:string;alt:string}};
type Run = {internal_workflow?:{active?:boolean};updated_at?:string;owner_user_id?:string|null;outcomes?:{period_start:string;period_end:string;qualified_enquiries:number}[];professional_review?:{reviewer_name:string;revision:number}|null;publication?:{url?:string;revision?:number};combined_version?:number;release_blockers?:string[];graphic_file?:string;graphic_revision?:number|null;fee_presentation?:{mode:'numeric_prices'|'approved_wording_only'}|null;editorial?:Editorial|null;research_packet?:{official_sources?:{id:string;url:string}[]};html:string;topic_map:object|null;analysis:Research|null;run_id:string;status:string;revision:number;tokens_used:number;error:string|null;brand_check?:QualityCheck|null;seo_check?:QualityCheck|null;briefing:{title:string;search_intent:string;editorial_notes:string[]}|null;review_history:{reviewer_name:string;action:string;feedback_notes:string}[]};
type Summary = Pick<Run,'run_id'|'status'|'revision'> & {updated_at:string;owner_user_id:string|null;access:string};
type Platform = 'website'|'linkedin'|'google_business_profile';
type SocialPost = {id:string;run_id:string;source_revision:number;platform:'linkedin'|'google_business_profile';text:string;status:string;link_target?:string;link_url?:string};
type ImageAsset = {id:string;run_id:string;content_id:string;source_revision:number;platform:Platform;version:number;status:string;parent_image_id:string|null;master_id:string|null;optimization_status:'legacy'|'processing'|'failed'|'completed';seo_filename:string;final_format:'webp'|'jpeg'|'png';optimized_file_size:number|null};
type ImageSet = {master:{id:string;version:number;source_revision:number};variants:ImageAsset[]};
type ImageOptions = {enabled:boolean;configured:boolean;demo_mode:boolean;request_max_usd:number|null;reason:string|null};
type Job = {id:string;run_id:string;kind:string;status:string;current_step:string;error:string|null;error_code?:string|null;requires_paid_retry?:boolean;source_revision?:number|null;result_revision?:number|null;created_at:string;updated_at?:string;error_info?:UserError|null};
const platformLabels:Record<Platform,string>={website:'Website',linkedin:'LinkedIn',google_business_profile:t("google_business_profile")};
// API codes stay stable. Interface labels are temporarily English.
const statusLabels: Record<string, string> = {
  pending: t("pending_review"),
  new: t("new"), data_loaded: t("source_data_loaded"), seo_analysis_done: t("seo_analysis_complete"),
  topic_map_done: t("topic_plan_saved"), briefing_done: t("briefing_saved"),
  awaiting_selection:t("select_a_recommendation"), recommendations_only:t("link_recommendations_saved"),
  draft_created: t("draft_saved"), brand_check_passed: t("brand_check_passed"),
  seo_check_passed: t("ready_for_review"), needs_revision: t("revision_required"),
  approved: t("approved"), rejected: t("rejected"), exported: t("exported"), failed: t("failed"),
};
const statusLabel = (status: string) => statusLabels[status] ?? t("status_unavailable");
// Provider messages are never displayed directly in the interface.

export default function ReviewPage() {
  const identity=useRef('');
  const [userId,setUserId]=useState('');
  const [accountView,setAccountView]=useState<'signin'|'register'|'bootstrap'>('signin');
  const [setupAvailable,setSetupAvailable]=useState(false);
  const [historyScope,setHistoryScope]=useState('mine');
  const [email,setEmail]=useState('');
  const [password,setPassword]=useState('');
  const [newPassword,setNewPassword]=useState('');
  const [authCode,setAuthCode]=useState('');
  const [challenge,setChallenge]=useState('');
  const [authStage,setAuthStage]=useState('');
  const [mfaSetup,setMfaSetup]=useState<{secret:string;uri:string}|null>(null);
  const [recoveryCodes,setRecoveryCodes]=useState<string[]>([]);
  const [csrf,setCsrf]=useState('');
  const [role,setRole]=useState('');
  const [users,setUsers]=useState<{id:string;email:string;name:string;role:string;enabled:number;registration_status:string}[]>([]);
  const [newUserEmail,setNewUserEmail]=useState('');
  const [newUserName,setNewUserName]=useState('');
  const [newUserRole,setNewUserRole]=useState('reviewer');
  const [professionalNotes,setProfessionalNotes]=useState('');
  const [publishedUrl,setPublishedUrl]=useState('');
  const [outcomeStart,setOutcomeStart]=useState('');
  const [outcomeEnd,setOutcomeEnd]=useState('');
  const [enquiries,setEnquiries]=useState(0);
  const [budget,setBudget]=useState<{period:string;limit_microusd:number;recorded_microusd:number;reserved_microusd:number}|null>(null);
  const [adminPassword,setAdminPassword]=useState('');
  const [adminCode,setAdminCode]=useState('');
  const [temporaryPassword,setTemporaryPassword]=useState('');
  const [reviewer,setReviewer]=useState('');
  const [jobs,setJobs]=useState<Job[]>([]);
  const [run,setRun]=useState<Run|null>(null);
  const [progress,setProgress]=useState<RunProgressData|null>(null);
  const [runBudget,setRunBudget]=useState('1.00');
  const [researchScope,setResearchScope]=useState('site');
  const [maxRunBudget,setMaxRunBudget]=useState(1);
  const [linkPolicy,setLinkPolicy]=useState<'strict'|'starter_library'>('starter_library');
  const viewRequest=useRef(0);
  const [runs,setRuns]=useState<Summary[]>([]);
  const [notes,setNotes]=useState('');
  const [checks,setChecks]=useState({facts:false,graphics:false,manual:false});
  useEffect(()=>setChecks({facts:false,graphics:false,manual:false}),[run?.run_id,run?.revision]);
  const [target,setTarget]=useState('content_agent');
  const [html,setHtml]=useState('');
  const [reviewFinding,setReviewFinding]=useState<PortalFinding|null>(null);
  const [saveNotice,setSaveNotice]=useState<{run_id:string;revision:number}|null>(null);
  const [previewReady,setPreviewReady]=useState('');
  const [previewRefresh,setPreviewRefresh]=useState(0);
  const selectedFinding=reviewFinding?.run_id===run?.run_id&&reviewFinding?.reviewed_revision===run?.revision?reviewFinding:null;
  const [previewIssue,setPreviewIssue]=useState('');
  const [plainPreview,setPlainPreview]=useState(false);
  const reviewSignature=progress?.findings?.map(item=>item.finding_id||item.code).join(',')||'';
  const previewKey=(run?.run_id||'')+':'+run?.revision+':'+(selectedFinding?.finding_id||'')+':'+reviewSignature;
  useEffect(()=>{
    setPreviewReady('');setPreviewIssue('');setPlainPreview(false);
    if(!run?.html||!reviewSignature||progress?.revision!==run.revision)return;
    let active=true;const actor=identity.current;
    const path='/runs/'+run.run_id+'/review-preview?revision='+run.revision+(selectedFinding?.finding_id?'&finding_id='+encodeURIComponent(selectedFinding.finding_id):'');
    void api(path).then(response=>response.text()).then(()=>{if(active&&identity.current===actor)setPreviewReady(previewKey);}).catch(()=>{if(active&&identity.current===actor)setPreviewIssue('Review highlights could not load. Your saved German draft is shown below; this does not mean server work failed.');});
    return()=>{active=false;};
  },[run?.run_id,run?.revision,selectedFinding?.finding_id,reviewSignature,userId,previewRefresh]);
  useEffect(()=>setReviewFinding(null),[run?.run_id,run?.revision]);
  const [map,setMap]=useState('');
  const [busy,setBusy]=useState(false);
  const unsavedEdit=useRef(false);
  useEffect(()=>{const listener=(e:Event)=>{unsavedEdit.current=(e as CustomEvent<boolean>).detail;};window.addEventListener('muster-unsaved-edit',listener);return()=>window.removeEventListener('muster-unsaved-edit',listener);},[]);
  const [error,setError]=useState<UserError|null>(null);
  const [connectionIssue,setConnectionIssue]=useState<UserError|null>(null);
  const [localReadiness,setLocalReadiness]=useState<{status:string;label:string}|null>(null);
  const [lastUpdated,setLastUpdated]=useState<string|null>(null);
  const [stageChoice,setStageChoice]=useState<{key:string;stage:number}|null>(null);
  const [icps,setIcps]=useState<{id:string;name:string}[]>([]);
  const [icp,setIcp]=useState('icp_1_buy_and_hold');
  const [seedTopic,setSeedTopic]=useState('Immobiliensteuerberater');
  const [posts,setPosts]=useState<SocialPost[]>([]);
  const [images,setImages]=useState<ImageAsset[]>([]);
  const [imageSets,setImageSets]=useState<ImageSet[]>([]);
  const [imageUrls,setImageUrls]=useState<Record<string,string>>({});
  const [pngRequested,setPngRequested]=useState(false);
  const [imageOptions,setImageOptions]=useState<ImageOptions|null>(null);
  const [imageChoice,setImageChoice]=useState<string|null>(null);
  const operationPending=useRef(false);
  useEffect(()=>{setImageChoice(null);setPngRequested(false);},[run?.run_id,run?.revision]);
  function clearPrivate() {
    viewRequest.current++;setProgress(null);setSaveNotice(null);
    setRun(null);setRuns([]);setJobs([]);setPosts([]);setImages([]);setImageSets([]);setHtml('');setMap('');setBudget(null);setLocalReadiness(null);setLastUpdated(null);setImageOptions(null);setUsers([]);setRecoveryCodes([]);setNotes('');setProfessionalNotes('');setPublishedUrl('');setAdminPassword('');setAdminCode('');setTemporaryPassword('');setImageChoice(null);setStageChoice(null);setOutcomeStart('');setOutcomeEnd('');setEnquiries(0);
    setImageUrls(old=>{Object.values(old).forEach(URL.revokeObjectURL);return {};});
  }
  function forgetSession(){identity.current='';setUserId('');setReviewer('');setRole('');setCsrf('');clearPrivate();}
  function acceptSession(session:{user_id:string;reviewer:string;role:string;csrf_token:string}) {
    if(identity.current!==session.user_id)clearPrivate();
    identity.current=session.user_id;setUserId(session.user_id);setReviewer(session.reviewer);setRole(session.role);setCsrf(session.csrf_token);setHistoryScope('mine');
  }
  async function api(path:string,init:RequestInit={}) {
    try{return await portalFetch(path,init,csrf);}catch(e){if(e instanceof PortalError&&e.status===401&&identity.current)forgetSession();throw e;}
  }
  async function operation(action:()=>Promise<void>) {
    if(operationPending.current)return;
    operationPending.current=true;
    setBusy(true);setError(null);
    try {await action();} catch(e) {setError(errorInfo(e));} finally {operationPending.current=false;setBusy(false);}
  }
  async function show(data:Run) {
    const actor=identity.current;
    if(!actor)return;
    const request=++viewRequest.current;
    const same=run?.run_id===data.run_id;
    if(unsavedEdit.current&&!same&&!window.confirm('You have unsaved changes. Discard them and open another run?'))return;
    if(unsavedEdit.current&&same&&run?.revision!==data.revision){setConnectionIssue({...localErrors.processing,title:'A newer revision is saved',message:'Your unsaved edit is still visible. Copy it before refreshing; saving against an older revision is blocked.',next_steps:['Preserve your changes, then reopen the saved run.']});return;}
    const optional=async<T,>(promise:Promise<T>,fallback:T):Promise<T>=>{try{return await promise;}catch(e){if(identity.current===actor&&request===viewRequest.current)setConnectionIssue(errorInfo(e));return fallback;}};
    localStorage.setItem('muster:'+actor+':last-run',data.run_id);
    const [draft,topics,postList,imageList,sets,nextProgress]=await Promise.all([
      optional(data.html ? api('/runs/'+data.run_id+'/article').then(r=>r.text()) : Promise.resolve(''),data.html||''),
      optional(data.topic_map ? api('/runs/'+data.run_id+'/topic-map').then(r=>r.text()) : Promise.resolve(''),same?map:''),
      optional(api('/runs/'+data.run_id+'/social-posts').then(r=>r.json()),same?posts:[]),
      optional(api('/runs/'+data.run_id+'/images').then(r=>r.json()),same?images:[]),
      optional(api('/runs/'+data.run_id+'/image-sets').then(r=>r.json()),same?imageSets:[]),
      optional(api('/runs/'+data.run_id+'/progress').then(r=>r.json()),null)]);
    if(identity.current!==actor||request!==viewRequest.current)return;
    setProgress(nextProgress|| (same?progress:null));
    setRun(data);setHtml(draft);setMap(topics);
    setPosts(postList);setImages(imageList);setImageSets(sets);
    const previews:Record<string,string>={};
    for(const item of imageList as ImageAsset[]) {
      if (!['completed','legacy'].includes(item.optimization_status)) continue;
      try {
        const blob=await (await api('/runs/'+data.run_id+'/images/'+item.id+'/file')).blob();
        previews[item.id]=URL.createObjectURL(blob);
      } catch { setConnectionIssue({...localErrors.processing,title:t("image_preview_unavailable"),message:t("the_text_draft_remains_available_one_saved_image_file_could_not_b"),next_steps:[t("refresh_the_run_or_check_the_local_image_files_with_your_administ")]}); }
    }
    if(identity.current!==actor){Object.values(previews).forEach(URL.revokeObjectURL);return;}
    setImageUrls(old=>{Object.values(old).forEach(URL.revokeObjectURL);return previews;});
  }
  async function load() {
    const actor=identity.current;
    const session=await(await api('/session')).json();
    if(!actor||session.user_id!==actor){forgetSession();throw new PortalError(localErrors.session,401);}
    const [nextBudget,options,audiences,saved,queued]=await Promise.all(['/budget','/image-generation/options','/icps','/runs','/jobs'].map(path=>api(path).then(r=>r.json())));
    if(identity.current!==actor)return {saved:[] as Summary[],queued:[] as Job[]};
    setBudget(nextBudget);setMaxRunBudget(nextBudget.run_limit_usd||1);setImageOptions(options);setIcps(audiences);setRuns(saved);setJobs(queued);setConnectionIssue(null);setLastUpdated(new Date().toISOString());
    if(run&&!saved.some((s:Summary)=>s.run_id===run.run_id)){setRun(null);setHtml('');setMap('');setPosts([]);setImages([]);setImageSets([]);setImageUrls(old=>{Object.values(old).forEach(URL.revokeObjectURL);return {};});}
    return {saved:saved as Summary[],queued:queued as Job[]};
  }
  async function queue(kind:string, extra:Record<string,unknown>={}) {
    const review=extra.review as {action:string;expected_revision:number}|undefined;
    const actor=identity.current;if(!actor)throw new PortalError(localErrors.session,401);
    const keyName='muster:'+actor+':pending-'+kind+'-'+(extra.run_id||'new')+'-'+(review?review.action+'-'+review.expected_revision:(extra.parent_id||extra.icp_id||''))+'-'+String(extra.png_requested||false)+(kind==='selection'?'-'+JSON.stringify(extra.selection):'');
    const requestId=localStorage.getItem(keyName)||crypto.randomUUID();
    localStorage.setItem(keyName,requestId);
    const result=await (await api('/jobs',{method:'POST',body:JSON.stringify({kind,idempotency_key:requestId,...extra})})).json() as Job;
    if(identity.current!==actor)return;
    localStorage.setItem('muster:'+actor+':last-run',result.run_id);
    localStorage.removeItem(keyName);
    await load();
    await show(await (await api('/runs/'+result.run_id)).json());
  }
  useEffect(()=>{
    let cancelled=false;
    async function restore(){
      try{
        const session=await (await portalFetch('/session')).json();
        if(!session.reviewer||cancelled)return;
        acceptSession(session);
        const {saved,queued}=await load();
        const active=queued.find(j=>['queued','running','paused'].includes(j.status));
        const selected=localStorage.getItem('muster:'+session.user_id+':last-run');
        const last=selected&&saved.some(item=>item.run_id===selected)?selected:active?.run_id;
        if(last&&!cancelled)await show(await (await api('/runs/'+last)).json());
      }catch(e){if(!(e instanceof PortalError&&e.status===401))setConnectionIssue(errorInfo(e));}
    }
    portalFetch('/auth/bootstrap/status').then(r=>r.json()).then(data=>{if(!cancelled)setSetupAvailable(data.setup_available);}).catch(e=>{if(!cancelled)setConnectionIssue(errorInfo(e));});
    void restore();
    return()=>{cancelled=true;};
  },[]);
  useEffect(()=>{
    if(!reviewer)return;
    const timer=setInterval(()=>{
      if(busy||operationPending.current)return;
      const viewGeneration=viewRequest.current;
      void (async()=>{try{const hadActive=jobs.some(j=>j.run_id===run?.run_id&&['queued','running'].includes(j.status));const {queued}=await load();if(viewGeneration!==viewRequest.current||operationPending.current)return;if(run){const actor=identity.current;const data=await(await api('/runs/'+run.run_id+'/progress')).json();if(identity.current!==actor||viewGeneration!==viewRequest.current||operationPending.current)return;setProgress(data);if(data.status!==run.status||data.revision!==run.revision||hadActive||queued.some(j=>j.run_id===run.run_id&&['queued','running'].includes(j.status)))await show(await(await api('/runs/'+run.run_id)).json());}}catch(e){setConnectionIssue(errorInfo(e));}})();
    },4000);
    return()=>clearInterval(timer);
  },[reviewer,run?.run_id,busy,jobs]);
  useEffect(()=>{if(!reviewer)return;let active=true;portalFetch('/system/readiness').then(r=>r.json()).then(data=>{if(active)setLocalReadiness(data);}).catch(e=>{if(active)setConnectionIssue(errorInfo(e));});return()=>{active=false;};},[reviewer]);
  async function review(action:string) {
    if(!run) return;
    if(action==='needs_revision'&&!window.confirm(account.paidCorrection))return;
    await queue('review',{run_id:run.run_id,review:{action,feedback_target_agent:target,feedback_notes:notes,expected_revision:run.revision,factual_review_confirmed:checks.facts,graphic_review_confirmed:checks.graphics,manual_upload_confirmed:checks.manual}});
    setNotes('');
  }
  async function saveEditorial(document:EditableEditorial,editNotes:string,editId:string,feePolicy?:{mode:'numeric_prices'|'approved_wording_only';confirmed:true}) {
    if(!run)return;
    await operation(async()=>{
      const data=await(await api('/runs/'+run.run_id+'/editorial',{method:'POST',body:JSON.stringify({
        edit_id:editId,expected_revision:run.revision,editorial:document,notes:editNotes,...(feePolicy?{fee_presentation:feePolicy}:{})})})).json();
      unsavedEdit.current=false;setSaveNotice({run_id:data.run_id,revision:data.revision});
      await load();await show(data);
    });
  }
  async function attachGraphic(file:File) {
    if(!run)return;
    if(file.size>=300000)throw new PortalError({...localErrors.validation,message:t("the_webp_infographic_must_be_smaller_than_300_kb")});
    const bytes=new Uint8Array(await file.arrayBuffer());
    let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
    const data=await(await api('/runs/'+run.run_id+'/graphic',{method:'POST',body:JSON.stringify({content_base64:btoa(binary),expected_revision:run.revision})})).json();
    await show(data);
  }
  async function download() {
    if(!run) return;
    const blob=await (await api('/runs/'+run.run_id+'/bundle')).blob();
    const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download='seo-'+run.run_id+'.zip';link.click();URL.revokeObjectURL(url);
  }
  async function professionalReview() {if(!run)return;const data=await(await api('/runs/'+run.run_id+'/professional-reviews',{method:'POST',body:JSON.stringify({expected_revision:run.revision,factual_review_confirmed:checks.facts,graphic_review_confirmed:checks.graphics,notes:professionalNotes})})).json();await show(data);setProfessionalNotes('');}
  async function recordOutcome() {if(!run)return;const data=await(await api('/runs/'+run.run_id+'/outcomes',{method:'POST',body:JSON.stringify({expected_revision:run.revision,period_start:outcomeStart,period_end:outcomeEnd,qualified_enquiries:enquiries})})).json();await show(data);}
  async function recordPublication() {if(!run)return;const data=await(await api('/runs/'+run.run_id+'/publication',{method:'POST',body:JSON.stringify({expected_revision:run.revision,url:publishedUrl,confirmed_live:true})})).json();await show(data);}
  async function linkArticle(id:string) {if(!run)return;await api('/runs/'+run.run_id+'/social-posts/'+id+'/article-link',{method:'POST',body:JSON.stringify({expected_revision:run.revision})});await show(run);}
  async function preparePosts() {if(!run)return;await queue('social',{run_id:run.run_id});}
  async function selectRecommendation(selection:Selection) {if(!run)return;await operation(async()=>{
    if(selection.mode==='links_only'){await queue('selection',{run_id:run.run_id,selection});return;}
    const result=await(await api('/runs/'+run.run_id+'/preflight?recommendation_id='+encodeURIComponent(selection.recommendation_id))).json();
    if(result.status==='blocked'){
      await show(run);
      throw new PortalError({...localErrors.conflict,title:t('preflight'),message:t('preflight_blocked'),next_steps:result.findings.filter((f:{blocking:boolean})=>f.blocking).map((f:{message:string;action:string})=>f.message+' '+f.action)},409);
    }
    await queue('selection',{run_id:run.run_id,selection});
  });}
  async function decidePost(id:string,action:'approved'|'rejected') {if(!run)return;await api('/runs/'+run.run_id+'/social-posts/'+id+'/reviews',{method:'POST',body:JSON.stringify({action})});await show(run);}
  async function createImageSet(parent?:ImageSet) {if(!run||!imageGenerationChosen||imageUnavailable||activeJob)return;await queue(parent?'image_set_variation':'image_set',{run_id:run.run_id,...(parent?{parent_id:parent.master.id}:{png_requested:pngRequested})});}
  async function createLegacyVariation(item:ImageAsset) {if(!run||!imageGenerationChosen||imageUnavailable||activeJob)return;await queue('single_image_variation',{run_id:run.run_id,parent_id:item.id});}
  async function retryProcessing(item:ImageAsset) {if(!run)return;const path=item.master_id?'/runs/'+run.run_id+'/image-sets/'+item.master_id+'/process/'+item.platform:'/runs/'+run.run_id+'/images/'+item.id+'/process';try{await api(path,{method:'POST'});}finally{await show(run);}}
  async function downloadImage(item:ImageAsset) {if(!run)return;const blob=await (await api('/runs/'+run.run_id+'/images/'+item.id+'/file')).blob();const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=item.seo_filename||item.id+'.jpg';link.click();URL.revokeObjectURL(url);}
  async function decideImage(id:string,action:'approved'|'rejected') {if(!run)return;await api('/runs/'+run.run_id+'/images/'+id+'/reviews',{method:'POST',body:JSON.stringify({action})});await show(run);}
  async function signIn(){const result=await (await api('/auth/login',{method:'POST',body:JSON.stringify({email,password})})).json();setPassword('');setChallenge(result.challenge);setAuthStage(result.stage);if(result.stage==='setup_mfa')setMfaSetup(await(await api('/auth/setup/mfa',{method:'POST',body:JSON.stringify({challenge:result.challenge})})).json());}
  async function setupPassword(){await api('/auth/setup/password',{method:'POST',body:JSON.stringify({challenge,password:newPassword})});setNewPassword('');setAuthStage('setup_mfa');setMfaSetup(await(await api('/auth/setup/mfa',{method:'POST',body:JSON.stringify({challenge})})).json());}
  async function finishSignIn(){const path=authStage==='mfa'?'/auth/verify':'/auth/setup/confirm';const result=await(await api(path,{method:'POST',body:JSON.stringify({challenge,code:authCode})})).json();acceptSession(result);setRecoveryCodes(result.recovery_codes||[]);setChallenge('');setAuthStage('');setMfaSetup(null);setAuthCode('');await load();}
  async function signOut(){await api('/session/logout',{method:'POST'});forgetSession();setEmail('');setPassword('');setAccountView('signin');setConnectionIssue(null);setError(null);}
  async function beginSetup(result:{challenge:string;stage:string}){setAccountView('signin');setSetupAvailable(false);setChallenge(result.challenge);setAuthStage(result.stage);if(result.stage==='setup_mfa')setMfaSetup(await(await api('/auth/setup/mfa',{method:'POST',body:JSON.stringify({challenge:result.challenge})})).json());}
  async function loadUsers(){setUsers(await(await api('/admin/users')).json());}
  async function createUser(){const result=await(await api('/admin/users',{method:'POST',body:JSON.stringify({email:newUserEmail,name:newUserName,role:newUserRole,admin_password:adminPassword,admin_code:adminCode})})).json();setTemporaryPassword(result.temporary_password);setNewUserEmail('');setNewUserName('');setAdminPassword('');setAdminCode('');await loadUsers();}
  async function adminAction(id:string,action:'reset'|'disable'|'enable'|'approve'|'reject'){const result=await(await api('/admin/users/'+id+'/'+action,{method:'POST',body:JSON.stringify({admin_password:adminPassword,admin_code:adminCode})})).json();setTemporaryPassword(result.temporary_password||'');setAdminPassword('');setAdminCode('');await loadUsers();}
  async function retryJob(id:string){
    const job=jobs.find(item=>item.id===id);
    if((job?.kind!=='research_repair'||job?.requires_paid_retry||job?.status==='paused'||job?.error_info?.retry==='may_charge'||['image_set','image_set_variation','single_image_variation'].includes(job?.kind||''))&&!window.confirm(t("retrying_may_submit_another_paid_request_saved_results_are_reused")))return;
    await api('/jobs/'+id+'/retry',{method:'POST'});await load();
  }
  async function retryQualityChecks(){
    if(!run||!window.confirm('Start paid QA for '+(progress?.review_plan?.remaining??'pending')+' units? Estimated maximum reservation: $'+((progress?.review_plan?.maximum_reservation_microusd??0)/1000000).toFixed(4)+'. Saved valid checks will be reused. Your draft will not be rewritten.'))return;
    await queue('quality_checks',{run_id:run.run_id,expected_revision:run.revision});
  }
  function renderImage(item:ImageAsset) {return <article key={item.id} className="asset"><h3>{platformLabels[item.platform]}  {t("version")} {item.version} · {item.optimization_status==='failed'?t("processing_failed"):item.optimization_status==='processing'?t("processing_pending"):statusLabel(item.status)}</h3>{item.seo_filename&&<p className="hint">{item.seo_filename}{item.optimized_file_size!==null?' · '+Math.round(item.optimized_file_size/1024)+' KB':''}</p>}{imageUrls[item.id]&&<img className="asset-image" src={imageUrls[item.id]} alt={t("ai_generated_image_for")+platformLabels[item.platform]}/>}<div>{!item.master_id&&<button disabled={busy||!!activeJob||!imageGenerationChosen||!!imageUnavailable} onClick={()=>operation(()=>createLegacyVariation(item))}>{t("generate_another_image")}</button>}{['failed','processing'].includes(item.optimization_status)&&<button disabled={busy} onClick={()=>operation(()=>retryProcessing(item))}>{t("retry_local_image_processing")}</button>}<button disabled={busy||!['completed','legacy'].includes(item.optimization_status)} onClick={()=>operation(()=>downloadImage(item))}>{t("download_image")}</button><button disabled={busy||item.status!=='pending'||!['completed','legacy'].includes(item.optimization_status)} onClick={()=>operation(()=>decideImage(item.id,'approved'))}>{t("approve")}</button><button className="secondary" disabled={busy||item.status!=='pending'||!['completed','legacy'].includes(item.optimization_status)} onClick={()=>operation(()=>decideImage(item.id,'rejected'))}>{t("reject")}</button></div></article>;}
  const socialReady=!!run&&(['linkedin','google_business_profile'] as Platform[]).every(p=>{
    const latest=[...posts].reverse().find(post=>post.platform===p&&post.source_revision===run.revision);
    return !!latest&&latest.status!=='rejected';
  });
  // Combined research has an empty editorial object until drafting completes.
  const claims=run?.editorial?.claims??[];
  const graphic=run?.editorial?.graphic;
  const activeJob=run&&jobs.find(j=>j.run_id===run.run_id&&['queued','running'].includes(j.status));
  const imageChoiceKey=run ? `${run.run_id}:${run.revision}:${run.status}` : '';
  const imageGenerationChosen=!!run&&imageChoice===imageChoiceKey;
  const imageUnavailable=!imageOptions ? t("loading_image_configuration")
    : !imageOptions.enabled||!imageOptions.configured ? t("image_generation_is_unavailable_check_system_status_for_configura")
    : !run||!['seo_check_passed','exported'].includes(run.status)||(!imageOptions.demo_mode&&run.status!=='exported') ? t("approve_and_export_the_article_locally_before_generating_images")
    : !socialReady ? t("prepare_both_current_social_drafts_for_linkedin_and_google_busine")
    : null;
  const stageKey=run ? `${run.run_id}:${run.revision}:${run.status}` : 'new';
  const suggestedStage=progress?.workflow&&progress.run_id===run?.run_id&&progress.revision===run?.revision?progress.workflow.workspace:!run||run.status==='awaiting_selection'||run.status==='recommendations_only'?0:run.status==='exported'?3:html&&run.status==='seo_check_passed'?2:1;
  const stage=stageChoice?.key===stageKey ? stageChoice.stage : suggestedStage;
  function navigateStage(workspace:number,step?:string){
    if(workspace!==1&&unsavedEdit.current&&!window.confirm('You have unsaved changes. Discard them and leave the draft?'))return;
    setStageChoice({key:stageKey,stage:workspace});
    if(run?.internal_workflow?.active&&step&&['professional','approval','export'].includes(step))step='internal';
    if(step)setTimeout(()=>document.getElementById('guided-'+step)?.scrollIntoView({block:'start'}),50);
  }
  const stageStateKeys=["stage_state_0","stage_state_1","stage_state_2","stage_state_3","stage_state_4"] as const;
  const checkChips=():StatusChip[]|undefined=>{
    const status=progress&&progress.run_id===run?.run_id&&progress.revision===run?.revision?progress.check_status:undefined;
    if(!status)return undefined;
    return [{label:t("brand_check")+': '+status.brand,kind:status.brand==='passed'?'pass':status.brand==='pending'?'warn':'fail'},
      {label:'SEO QA: '+status.seo,kind:status.seo==='passed'?'pass':status.seo==='pending'||status.seo==='processing'?'warn':'fail'},
      {label:'Draft: '+status.draft,kind:status.draft.indexOf('Approved')===0?'pass':status.draft.indexOf('No')===0?'info':'warn'}];
  };
  const canDraft=!!run&&(role==='admin'||run.owner_user_id===userId||(['Alex','Sam'].includes(reviewer)&&role==='reviewer'));
  const canFinalReview=['Alex','Sam'].includes(reviewer)&&['reviewer','admin'].includes(role);
  const currentChecks=progress&&progress.run_id===run?.run_id&&progress.revision===run?.revision?progress.check_status:null;
  const qualityRetryAvailable=!!run&&!!html&&['failed','draft_created','brand_check_passed'].includes(run.status)&&!activeJob&&!jobs.some(j=>j.run_id===run.run_id&&j.status==='paused');
  const qualityCorrectionAvailable=!!run&&!!html&&run.status==='failed'&&!![run.brand_check,run.seo_check].find(check=>check&&!check.passed)&&!activeJob&&!jobs.some(j=>j.run_id===run.run_id&&j.status==='paused');
  const jobKindLabels:Record<string,string>={start:'SEO research',research_refresh:t("new_research"),research_repair:t("check_website_evidence"),selection:t("selected_draft"),review:t("review"),quality_checks:t("quality_checks"),social:t("german_social_drafts"),image_set:'Image set',image_set_variation:t("new_image_set"),single_image_variation:t("new_image_version")};
  const stepLabels:Record<string,string>={seo_strategy_agent:t("analyze_seo_evidence"),topic_map_agent:t("create_topic_plan"),briefing_agent:t("prepare_briefing"),content_agent:t("write_german_article"),brand_agent:t("check_brand_rules"),seo_qa_agent:t("check_seo_and_evidence"),social_content:t("prepare_social_content"),image_generation:t("create_images")};
  return <main className="workspace">

    <header><p className="eyebrow">{t("muster_partner")}</p><h1>{t("content_workspace")}</h1><p>{t("research_create_and_review_german_content_with_confidence")}</p></header>
    <section className={t("panel_account_panel")+(reviewer?" signed-in":"")}>{reviewer?<><p>{t("signed_in_as")} <strong>{reviewer}</strong></p><button className="secondary" disabled={busy} onClick={()=>operation(signOut)}>{t("sign_out")}</button></>:accountView!=='signin'?<RegistrationForm bootstrap={accountView==='bootstrap'} onBack={()=>setAccountView('signin')} onSetup={beginSetup}/>:authStage==='setup_password'?<><h2>{t("choose_a_new_password")}</h2><label>{t("new_password_at_least_12_characters")}<input type="password" autoComplete="new-password" value={newPassword} onChange={e=>setNewPassword(e.target.value)}/></label><button disabled={busy||newPassword.length<12} onClick={()=>operation(setupPassword)}>{t("continue")}</button></>:authStage==='setup_mfa'?<><h2>{t("set_up_authenticator")}</h2><p>{t("add_this_key_to_your_authenticator_app")}</p><code>{mfaSetup?.secret}</code>{!mfaSetup&&<p role="status">{account.preparingMfa}</p>}<p className="hint">{t("or_use_this_setup_uri_in_your_authenticator_app")} {mfaSetup?.uri}</p><label>{t("six_digit_code")}<input inputMode="numeric" autoComplete="one-time-code" value={authCode} onChange={e=>setAuthCode(e.target.value)}/></label><button disabled={busy||!mfaSetup||authCode.length!==6} onClick={()=>operation(finishSignIn)}>{t("confirm_setup")}</button></>:authStage==='mfa'?<><h2>{t("authenticator_code")}</h2><label>{t("authenticator_or_recovery_code")}<input autoComplete="one-time-code" value={authCode} onChange={e=>setAuthCode(e.target.value)}/></label><button disabled={busy||!authCode} onClick={()=>operation(finishSignIn)}>{t("sign_in")}</button></>:<><label>{t("email_address")}<input type="email" autoComplete="username" value={email} onChange={e=>setEmail(e.target.value)}/></label><label>{t("password")}<input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)}/></label><button disabled={busy||!email||!password} onClick={()=>operation(signIn)}>{t("sign_in")}</button><button className="secondary" disabled={busy} onClick={()=>{setPassword('');setAccountView('register');}}>{account.create}</button>{setupAvailable&&<button className="secondary" disabled={busy} onClick={()=>{setPassword('');setAccountView('bootstrap');}}>{account.setup}</button>}<p className="hint">{account.registrationHelp}</p></>}{!reviewer&&authStage&&<button className="secondary" disabled={busy} onClick={()=>{setAuthStage('');setChallenge('');setAuthCode('');setMfaSetup(null);setNewPassword('');}}>{t("restart_sign_in")}</button>}</section>
    {recoveryCodes.length>0&&<section className="panel"><h2>{t("recovery_codes")}</h2><p>{t("save_these_recovery_codes_securely_now_each_code_works_only_once")}</p><pre>{recoveryCodes.join('\n')}</pre><button onClick={()=>setRecoveryCodes([])}>{t("i_have_saved_the_codes")}</button></section>}
    {reviewer&&role==='admin'&&<details className="panel"><summary>{account.accounts}</summary><button disabled={busy} onClick={()=>operation(loadUsers)}>{t("load_accounts")}</button><p className="hint">{account.qualifications}</p><label>{t("name")}<input value={newUserName} onChange={e=>setNewUserName(e.target.value)}/></label><label>{t("email_address")}<input type="email" value={newUserEmail} onChange={e=>setNewUserEmail(e.target.value)}/></label><label>{t("account_role")}<select value={newUserRole} onChange={e=>setNewUserRole(e.target.value)}><option value="member">{account.member}</option><option value="reviewer">{t("editorial_reviewer")}</option><option value="tax_adviser">{t("tax_adviser_professional_review")}</option></select></label><label>{t("administrator_password")}<input type="password" autoComplete="current-password" value={adminPassword} onChange={e=>setAdminPassword(e.target.value)}/></label><label>{t("current_authenticator_code")}<input inputMode="numeric" value={adminCode} onChange={e=>setAdminCode(e.target.value)}/></label><button disabled={busy||!newUserName||!newUserEmail||!adminPassword||adminCode.length!==6} onClick={()=>operation(createUser)}>{t("create_reviewer_account")}</button>{temporaryPassword&&<p>{t("one_time_temporary_password")} <code>{temporaryPassword}</code> <button onClick={()=>setTemporaryPassword('')}>{t("hide")}</button></p>}{users.map(u=><article key={u.id} className="asset"><strong>{u.name}</strong> · {u.email} · {u.registration_status==='pending'?account.pendingApproval:u.registration_status==='rejected'?account.rejected:u.enabled?t("active"):t("disabled")}{u.registration_status==='pending'&&<div><button disabled={busy||!adminPassword||adminCode.length!==6} onClick={()=>operation(()=>adminAction(u.id,'approve'))}>{account.approve}</button><button className="secondary" disabled={busy||!adminPassword||adminCode.length!==6} onClick={()=>operation(()=>adminAction(u.id,'reject'))}>{account.reject}</button></div>}{u.registration_status==='active'&&['member','reviewer','tax_adviser'].includes(u.role)&&<div><button disabled={busy||!adminPassword||adminCode.length!==6} onClick={()=>operation(()=>adminAction(u.id,'reset'))}>{t("reset_access")}</button><button disabled={busy||!adminPassword||adminCode.length!==6} onClick={()=>operation(()=>adminAction(u.id,u.enabled?'disable':'enable'))}>{u.enabled?t("disable"):t("enable")}</button></div>}</article>)}</details>}
    <ErrorNotice admin={role==='admin'} info={error} onDismiss={()=>setError(null)}/>
    {connectionIssue&&<div className="connection-notice" role="status"><strong>{connectionIssue.title}</strong><p>{connectionIssue.message}</p><p>{connectionIssue.next_steps[0]}</p></div>}
    {reviewer&&lastUpdated&&<p className="connection-meta">{t("last_successful_refresh")} {new Date(lastUpdated).toLocaleTimeString('en-GB')}. {connectionIssue?t("showing_the_last_saved_view"):t("saved_status_is_up_to_date")}</p>}
    {reviewer&&localReadiness?.status==='needs_attention'&&<div className="connection-notice" role="status"><strong>{t("configuration_needs_attention_before_a_new_run")}</strong><p>{t("open")} <a href="/system">{t("system_status")}</a>  {t("for_the_required_corrections_then_reload_this_workspace_your_save")}</p></div>}
    {run&&<RunStatusBar runId={run.run_id} revision={run.revision} status={currentChecks?.summary||statusLabel(run.status)} costs={progress&&progress.run_id===run.run_id&&progress.revision===run.revision?progress.costs:null} currentStage={stage} suggestedStage={suggestedStage} onNavigate={navigateStage}/>}
    {reviewer&&<section className="panel stage-panel" hidden={!!run&&stage!==0}><h2>{t("start_a_new_run")}</h2><label>{t("research_scope")}<select value={researchScope} onChange={e=>setResearchScope(e.target.value)}><option value="site">{t("entire_website")}</option><option value="icp_1">{t("private_property_investors_buy_hold")}</option></select></label><label>{t("audience")}<select value={icp} onChange={e=>setIcp(e.target.value)}>{(icps.length?icps:[{id:'icp_1_buy_and_hold',name:t("private_property_investors_buy_hold")}]).map(i=><option key={i.id} value={i.id}>{i.id==='icp_1_buy_and_hold'?t("private_property_investors_buy_hold"):i.name}</option>)}</select></label><label>{t("main_topic_german")}<input value={seedTopic} maxLength={120} onChange={e=>setSeedTopic(e.target.value)}/></label><label>{t("internal_link_policy")}<select value={linkPolicy} onChange={e=>setLinkPolicy(e.target.value as 'strict'|'starter_library')}><option value="starter_library">{t("starter_library")}</option><option value="strict">{t("strict_link_policy")}</option></select></label><p className="hint">{t(linkPolicy==='starter_library'?"starter_library_help":"strict_policy_help")}</p><label>{t("new_run_budget")}<input type="number" min="0.01" max={maxRunBudget} step="0.01" value={runBudget} onChange={e=>setRunBudget(e.target.value)}/></label><button disabled={seedTopic.trim().length<3||!Number(runBudget)||Number(runBudget)<=0||Number(runBudget)>maxRunBudget||busy||localReadiness?.status==='needs_attention'||jobs.some(j=>['queued','running'].includes(j.status))} onClick={()=>operation(()=>queue('start',{icp_id:icp,seed_topic:seedTopic,run_budget_usd:Number(runBudget),research_scope:researchScope,internal_link_policy:linkPolicy}))}>{t("start_research")} · May incur a charge</button><p className="hint">{busy||jobs.some(j=>["queued","running"].includes(j.status))?"Wait for active work to finish.":localReadiness?.status==="needs_attention"?"Ask the administrator to resolve the configuration shown in System status.":seedTopic.trim().length<3?"Enter a main topic with at least 3 characters.":!Number(runBudget)||Number(runBudget)<=0||Number(runBudget)>maxRunBudget?"Choose a positive allowance within the application limit.":"Starting research uses paid integrations within the saved allowance. No publication occurs."}</p><p className="hint">{t("the_server_continues_processing_if_you_close_this_page_reopen_the")}</p></section>}
    {reviewer&&(budget||jobs.length>0)&&<details className="panel workspace-strip" open={!run}><summary>{t("workspace_strip_summary")}</summary>
      {budget&&<div className="strip-item"><h2>{t("monthly_budget")} {budget.period}  {t("utc")}</h2><p>{t("recorded")} {(budget.recorded_microusd/1000000).toFixed(2)} {t("usd_reserved")} {(budget.reserved_microusd/1000000).toFixed(2)} {t("usd_limit")} {(budget.limit_microusd/1000000).toFixed(2)}  {t("usd")}</p><p className="hint">{t("unknown_costs_stay_reserved_check_provider_invoices_and_account_l")}</p></div>}
      {jobs.length>0&&<details className="strip-item"><summary>{t("task_history_and_previous_attempts")}</summary><p className="hint">{t("history_explanation")}</p>{jobs.filter(job=>!run||job.run_id===run.run_id).slice(0,12).map(job=><article className="asset" key={job.id}><strong>{jobKindLabels[job.kind]||'Task'} · {job.status==='queued'?t("queued"):job.status==='running'?t("running"):job.status==='completed'?t("completed"):job.status==='paused'?t("your_decision_required"):job.error_code==='quality_failed'?t("needs_correction"):t("failed")}</strong><p className="hint">{stepLabels[job.current_step]||'Ready'} · {new Date(job.created_at).toLocaleString('en-GB')} · {job.source_revision!=null?t("attempt_revision")+' '+job.source_revision:t("revision_not_recorded")}</p>{job.error&&run?.run_id===job.run_id&&((job.result_revision!=null&&job.result_revision<run.revision)||((job.updated_at||job.created_at)<(run.updated_at||'')))?<details><summary>{t("historical_attempt")}</summary><ErrorNotice admin={role==='admin'} info={job.error_info||localErrors.processing}/></details>:<ErrorNotice admin={role==='admin'} info={job.error ? job.error_info||localErrors.processing:null}/>}<button disabled={busy} onClick={()=>operation(async()=>show(await(await api('/runs/'+job.run_id)).json()))}>{t("open_run")}</button>{['paused','failed'].includes(job.status)&&!['quality_failed','quality_evidence'].includes(job.error_code||'')&&<button disabled={busy} onClick={()=>operation(()=>retryJob(job.id))}>{t("retry_task")}</button>}</article>)}</details>}
      <div className="strip-item saved-runs"><h2>{account.history}</h2><div className="history-tabs" role="group" aria-label={account.historyFilters}>{[['mine',account.my],['shared',account.shared],...(role==='admin'?[['all',account.all]]:[])].map(([value,label])=><button key={value} className={historyScope===value?'':'secondary'} aria-pressed={historyScope===value} onClick={()=>setHistoryScope(value)}>{label}</button>)}</div>{runs.filter(s=>historyScope==='all'||(historyScope==='mine'?s.owner_user_id===userId:s.access==='shared'&&s.owner_user_id!==userId)).map(s=><button className="run" key={s.run_id} disabled={busy} onClick={()=>operation(async()=>show(await(await api('/runs/'+s.run_id)).json()))}>{s.run_id.slice(0,8)} · {statusLabel(s.status)} {t("revision")} {s.revision}<small> · {new Date(s.updated_at).toLocaleString('en-GB')}</small></button>)}{!runs.some(s=>historyScope==='all'||(historyScope==='mine'?s.owner_user_id===userId:s.access==='shared'&&s.owner_user_id!==userId))&&<p className="hint">{account.empty}</p>}</div>
    </details>}
    {run&&(role==='admin'||run.owner_user_id===userId)&&<RunSharing key={userId+':'+run.run_id} runId={run.run_id} admin={role==='admin'} onChange={async()=>{await load();await show(await(await api('/runs/'+run.run_id)).json());}}/>}
    {run&&progress?.workflow&&progress.revision===run.revision&&<GuidedWorkflow data={progress.workflow} chips={checkChips()} stateLabel={progress.run_id===run.run_id&&progress.revision===run.revision?progress.state:undefined} costs={progress.run_id===run.run_id&&progress.revision===run.revision?progress.costs:null} onNavigate={navigateStage} onAction={()=>{if(run.status==='exported')void operation(download);else navigateStage(progress.workflow!.workspace,progress.workflow!.primary_action.id==='internal'?'internal':progress.workflow!.current_step);}}/>}
    {run&&saveNotice?.run_id===run.run_id&&saveNotice?.revision===run.revision&&<p className="connection-notice" role="status">Changes saved locally as revision {run.revision} — no AI request was made. Semantic review remains pending.</p>}
    {run&&progress?.run_id===run.run_id&&progress.revision===run.revision&&<RunProgress data={progress} admin={role==='admin'} onShow={finding=>{setReviewFinding({...finding,run_id:run.run_id});setStageChoice({key:stageKey,stage:1});setTimeout(()=>document.getElementById('guided-draft')?.scrollIntoView({block:'start'}),50);}}/>}
    {run?.editorial?.modules&&<InternalWorkflow key={'internal-'+run.run_id} runId={run.run_id} revision={run.revision} allowed={role==='admin'||run.owner_user_id===userId} busy={busy||!!activeJob} onChanged={async()=>{await show(await (await api('/runs/'+run.run_id)).json());}}/>}
    {run?.editorial?.modules&&<SoloEvaluation key={run.run_id} runId={run.run_id} revision={run.revision} allowed={canDraft} busy={busy||!!activeJob} onSubmitted={async()=>{await load();await show(await (await api('/runs/'+run.run_id)).json());}}/>}
    {run && <><p className="stage-state">{t(stageStateKeys[stage]||"stage_state_0")}</p><section className="panel run-header"><div className="run-header-row"><h2>{run.briefing?.title ?? t("current_run")}</h2><div className="run-header-actions"><button disabled={busy} onClick={()=>operation(async()=>show(await (await api('/runs/'+run.run_id)).json()))}>{t("refresh_saved_status")} · Free</button><button disabled={busy||(!html&&!run.analysis?.recommendations?.length)} onClick={()=>operation(download)}>{html?(run.status!=='exported'?t("download_draft_package_not_approved"):t("download_html_package")):t("download_recommendation_report")}</button></div></div>{!canDraft&&<p className="hint">{account.draftingPermission}</p>}<p className="hint" key={run.run_id+':'+run.status+':'+run.revision+':'+run.tokens_used}>{t("status")} <strong>{currentChecks?.summary||(html?"Draft saved — QA status unavailable":statusLabel(run.status))}</strong>  {t("revision")} {run.revision}  {t("tokens")} {run.tokens_used}</p>{run.error&&<ErrorNotice admin={role==='admin'} info={jobs.find(j=>j.run_id===run.run_id&&j.error)?.error_info||localErrors.processing}/>}<p>{run.briefing?.search_intent&&<span lang="de" className="evidence-label">{t("german_search_intent")} {run.briefing.search_intent}</span>}</p>{qualityRetryAvailable&&<p className="hint">{!canDraft?"The owner or final reviewer must request this check.":progress?.qa_readiness?.ready===false?progress.qa_readiness.reason:progress?.review_plan&&!progress.review_plan.blocked?`${progress.review_plan.remaining} review units need a paid request (maximum reservation ${((progress.review_plan.maximum_reservation_microusd||0)/1000000).toFixed(4)} USD); saved units are reused and the draft is not rewritten.`:"Inspect the estimate, then confirm to start. Saved units are reused and the draft is not rewritten."}</p>}{qualityRetryAvailable&&<button disabled={busy||!canDraft||progress?.qa_readiness?.ready===false} onClick={()=>operation(retryQualityChecks)}>{t("rerun_quality_checks_only")} · May incur a charge</button>}{!progress&&([[t("brand_check"),run.brand_check],[t("seo_check"),run.seo_check]] as const).map(([label,check])=>check&&!check.passed&&<details open key={label}><summary>{label}{t("saved_german_review_findings")}</summary><ul>{check.errors.map((issue,i)=><li key={i}><ReviewNote text={issue}/></li>)}</ul></details>)}</section>
    {stage===0&&run.status!=='awaiting_selection'&&<section className="panel"><h2>{t("research_is_saved")}</h2><p>{t("this_run_has_already_moved_beyond_topic_selection_review_the_germ")}</p></section>}
    {run.status==='awaiting_selection'&&run.analysis&&<div hidden={stage!==0}><SEORecommendations key={run.run_id+'-'+run.revision} analysis={run.analysis} revision={run.revision} disabled={busy||!!activeJob||!canDraft} onSelect={selectRecommendation}/></div>}
    {run.status==='recommendations_only'&&<section className="panel stage-panel" hidden={stage!==0}><h2>{t("link_recommendation_report_saved")}</h2><p>{t("no_article_was_generated_for_this_run_download_the_report_and_rev")}</p></section>}
    {stage===1&&!html&&<section className="panel"><h2>{t("article_draft_not_available_yet")}</h2><p>{run.status==='awaiting_selection'?t("choose_a_topic_before_drafting"):activeJob?t("draft_work_is_still_processing"):t("inspect_saved_task_before_drafting")}</p></section>}
    {stage===1&&qualityCorrectionAvailable&&!progress&&<section className="panel" role="status"><h2>{t("draft_saved_corrections_required")}</h2><p>{t("saved_draft_correction_help")}</p><details open><summary>{t("saved_draft_findings")}</summary><ul>{[...new Set([...(run.brand_check?.errors||[]),...(run.seo_check?.errors||[]),...(run.release_blockers||[])])].map((finding,index)=><li key={index}><ReviewNote text={finding}/></li>)}</ul></details><p className="hint">{t("business_inputs_help")}</p></section>}
    {html && <section id="guided-draft" className="panel stage-panel" hidden={stage!==1}><h2>{t("german_article_draft")}</h2><p className="hint">Review highlights appear only here; your saved German draft and downloads remain unchanged.</p>
    {selectedFinding?.location?.field&&<div className={'qa-finding qa-'+selectedFinding.display_kind}><strong>{selectedFinding.location.field}</strong><p lang="de">{String((run.editorial as Record<string,unknown>)?.[selectedFinding.location.field]||'')}</p></div>}
    {previewIssue&&<div className="connection-notice" role="status"><p>{previewIssue}</p><button className="secondary" onClick={()=>setPreviewRefresh(value=>value+1)}>Retry highlights · Free</button></div>}
    <button className="secondary" onClick={()=>setPlainPreview(!plainPreview)}>{plainPreview?'Show review highlights · Free':'Show saved draft without highlights · Free'}</button>
    {previewReady===previewKey&&!plainPreview?<iframe key={run.run_id+':'+run.revision+':'+(selectedFinding?.finding_id||'')+':'+reviewSignature} title={t("german_article_preview")} sandbox="" lang="de" src={'/api/runs/'+run.run_id+'/review-preview?revision='+run.revision+(selectedFinding?.finding_id?'&finding_id='+encodeURIComponent(selectedFinding.finding_id):'')+(selectedFinding?.location?.status==='exact'&&!selectedFinding?.location?.field?'#finding-'+selectedFinding?.finding_id:selectedFinding?.location?.module_id?'#section-'+selectedFinding.location.module_id:'')}/>:<iframe title={t("german_article_preview")} sandbox="" lang="de" srcDoc={html}/>}
    </section>}

    {stage===1&&canDraft&&run.combined_version&&run.editorial?.modules&&<EditorialEditor key={run.run_id+':'+run.revision} document={run.editorial as EditableEditorial} feeMode={run.fee_presentation?.mode} busy={busy||!!activeJob||jobs.some(job=>job.run_id===run.run_id&&job.status==='paused')} onSave={saveEditorial}/>}
    {map && <details className="panel stage-panel" hidden={stage!==1}><summary>{t("german_topic_plan_and_internal_links")}</summary><iframe title={t("german_topic_plan")} sandbox="" lang="de" srcDoc={map}/></details>}
    <section className="panel stage-panel" hidden={stage!==3}><h2>{t("german_social_drafts")}</h2><p>{t("prepare_these_after_article_approval_and_review_each_separately_t")}</p><button disabled={busy||!['seo_check_passed','exported'].includes(run.status)||(!!run.combined_version&&run.status!=='exported')} onClick={()=>operation(preparePosts)}>{t("prepare_linkedin_and_google_business_profile_drafts")} · May incur a charge</button><p className="hint">Available after approved local export. The owner or final reviewer requests social drafts; every upload stays manual.</p>{posts.filter(p=>p.source_revision===run.revision).map(p=><article key={p.id} className="asset"><h3>{platformLabels[p.platform]} · {statusLabel(p.status)}</h3><p lang="de" className="generated-content" style={{whiteSpace:"pre-wrap"}}>{p.text}</p>{run.publication?.revision===run.revision&&<button disabled={busy||!canFinalReview||p.link_url===run.publication?.url} onClick={()=>operation(()=>linkArticle(p.id))}>{t("link_published_article_requires_renewed_approval")}</button>}<button disabled={busy||p.status!=='pending'||(!!run.combined_version&&(!canFinalReview||run.status!=='exported'))} onClick={()=>operation(()=>decidePost(p.id,'approved'))}>{t("approve")}</button><button className="secondary" disabled={busy||p.status!=='pending'||(!!run.combined_version&&!canFinalReview)} onClick={()=>operation(()=>decidePost(p.id,'rejected'))}>{t("reject")}</button></article>)}</section>
    <section className="panel stage-panel" hidden={stage!==3}><h2>{t("images_optional")}</h2>
      <fieldset disabled={busy||!!activeJob} aria-describedby="image-choice-help"><legend>{t("would_you_like_to_generate_ai_images")}</legend>
        <label><input type="radio" name="image-choice" checked={!imageGenerationChosen} onChange={()=>setImageChoice(null)}/>{t("no_ai_images")}</label>
        <label><input type="radio" name="image-choice" checked={imageGenerationChosen} onChange={()=>setImageChoice(imageChoiceKey)}/>{t("generate_ai_images")}</label>
      </fieldset>
      <p className="hint" id="image-choice-help">{t("selecting_this_option_does_not_submit_a_request_skipping_generati")}</p>
      {imageGenerationChosen&&<div>
        <p>{t("one_master_image_is_adapted_for_the_website_1536_1024_linkedin_12")}</p>
        {imageOptions?.request_max_usd!=null&&<p className="hint">{t("each_generation_attempt_reserves")} {imageOptions.request_max_usd.toLocaleString('en-GB',{style:'currency',currency:'USD'})} {t("against_the_shared_run_and_monthly_budgets_this_is_not_an_exact_p")}</p>}
        {imageUnavailable&&<p role="status">{imageUnavailable}</p>}
        {activeJob&&<p role="status">{t("wait_for_the_current_task_to_finish")}</p>}
        <label><input type="checkbox" checked={pngRequested} disabled={busy||!!activeJob} onChange={e=>setPngRequested(e.target.checked)}/>{t("use_png_for_google_business_profile")}</label>
        <button disabled={busy||!!activeJob||!!imageUnavailable} onClick={()=>operation(()=>createImageSet())}>{t("generate_image_set")}</button>
      </div>}
      {imageSets.filter(s=>s.master.source_revision===run.revision).map(set=><div className="asset" key={set.master.id}><h3>{t("image_set_version")} {set.master.version}</h3>{imageGenerationChosen&&<button disabled={busy||!!activeJob||!!imageUnavailable} onClick={()=>operation(()=>createImageSet(set))}>{t("generate_another_image_set")}</button>}{set.variants.map(renderImage)}</div>)}
      {images.some(i=>!i.master_id)&&<details><summary>{t("earlier_individual_images")}</summary>{images.filter(i=>!i.master_id&&i.source_revision===run.revision).map(renderImage)}</details>}
    </section>
    {!!run.combined_version&&graphic&&<section className="panel stage-panel" id="guided-infographic" hidden={stage!==2}><h2>{run.internal_workflow?.active?"Current infographic":t("professional_evidence_and_required_infographic")}</h2><InfographicUpload revision={run.revision} saved={!!run.graphic_file&&run.graphic_revision===run.revision} html={run.html} alt={graphic.alt} busy={busy} disabledReason={!canDraft?"The run owner or an authorized final reviewer can upload the infographic.":run.status!=="seo_check_passed"?"Complete valid automated QA before uploading.":activeJob?"Wait for the current task to finish before uploading.":null} onUpload={file=>operation(()=>attachGraphic(file))}/><p>{run.internal_workflow?.active?"This infographic is attached for internal review. No professional approval is implied.":t("website_linkedin_and_google_business_profile_content_is_approved_")}</p>{(run.release_blockers||[]).length>0&&<details><summary>{t("outstanding_requirements_before_approval")}</summary><ul>{run.release_blockers?.map((b,i)=><li key={i}><ReviewNote text={b}/></li>)}</ul></details>}<details><summary>{t("claims_and_sources")}{claims.length})</summary>{claims.map(c=><article className="asset" key={c.id}><h3>{c.text}</h3><blockquote>{c.quote}</blockquote><p>{t("tax_year")} {c.tax_year} · {c.conditions}</p>{run.research_packet?.official_sources?.filter(x=>x.id===c.source_id).map(x=><a key={x.id} href={x.url} target="_blank" rel="noreferrer">{t("open_official_source")}</a>)}</article>)}</details><details><summary>{t("german_notebooklm_infographic_brief")}</summary><p>{graphic.title}</p><pre style={{whiteSpace:'pre-wrap'}}>{graphic.notebooklm_prompt}</pre></details>{!run.internal_workflow?.active&&<><label><input type="checkbox" checked={checks.facts} onChange={e=>setChecks({...checks,facts:e.target.checked})}/>{t("the_professional_review_of_the_full_text_and_every_tax_claim_is_c")}</label><label><input type="checkbox" checked={checks.graphics} onChange={e=>setChecks({...checks,graphics:e.target.checked})}/>{t("i_have_checked_the_infographic_for_figures_spelling_and_consisten")}</label><label><input type="checkbox" checked={checks.manual} onChange={e=>setChecks({...checks,manual:e.target.checked})}/>{t("i_confirm_that_all_publishing_will_be_manual_after_approval")}</label></>}</section>}
    {!!run.combined_version&&<section className="panel stage-panel" id="guided-professional" hidden={stage!==2||!!run.internal_workflow?.active}><h2>{t("professional_attestation")}</h2>{run.professional_review?.revision===run.revision?<p>{t("attestation_recorded_by")} {run.professional_review.reviewer_name}{t("changes_to_text_graphics_or_sources_require_a_new_attestation")}</p>:<p>{t("an_authorized_tax_adviser_account_must_attest_to_the_text_sources")}</p>}<p className="hint">{t("required_current_infographic_completed_evidence_checks_and_an_aut")}</p>{role==='tax_adviser'&&<><label>{t("professional_review_note")}<textarea rows={3} maxLength={2000} value={professionalNotes} onChange={e=>setProfessionalNotes(e.target.value)}/></label><button disabled={busy||run.status!=='seo_check_passed'||!checks.facts||!checks.graphics||professionalNotes.trim().length<10||!!run.release_blockers?.length} onClick={()=>operation(professionalReview)}>{t("record_professional_attestation")} · Free</button><p className="hint">{run.status!=="seo_check_passed"?"Complete valid automated QA first.":run.release_blockers?.length?"Resolve the release checklist and upload the current infographic first.":"Confirm text/source and infographic review, then enter a professional note of at least 10 characters."}</p></>}</section>}
    {stage===4&&run.status!=='exported'&&<section className="panel"><h2>{t("local_approval_is_required_first")}</h2><p>{t("complete_professional_review_and_final_approval_before_recording_")}</p></section>}
    {run.status==='exported'&&<section className="panel stage-panel" hidden={stage!==4}><h2>{t("record_manual_publication")}</h2><p>{t("prepare_the_social_drafts_first_after_manually_publishing_to_word")}</p>{run.publication?.url&&<p>{t("recorded_url")} {run.publication.url}</p>}<label>{t("published_https_url")}<input type="url" value={publishedUrl} onChange={e=>setPublishedUrl(e.target.value)}/></label><button disabled={busy||!publishedUrl||!['Alex','Sam'].includes(reviewer)} onClick={()=>operation(recordPublication)}>{t("confirm_this_page_has_been_published_manually")}</button></section>}
    {run.publication?.revision===run.revision&&<section className="panel stage-panel" hidden={stage!==4}><h2>{t("record_qualified_icp1_enquiries")}</h2><p>{t("manually_count_qualified_enquiries_for_a_completed_measurement_pe")}</p><label>{t("from")}<input type="date" value={outcomeStart} onChange={e=>setOutcomeStart(e.target.value)}/></label><label>{t("to")}<input type="date" value={outcomeEnd} onChange={e=>setOutcomeEnd(e.target.value)}/></label><label>{t("qualified_enquiries")}<input type="number" min={0} max={100000} value={enquiries} onChange={e=>setEnquiries(Number(e.target.value))}/></label><button disabled={busy||!outcomeStart||!outcomeEnd||!['Alex','Sam'].includes(reviewer)} onClick={()=>operation(recordOutcome)}>{t("save_measurement")}</button>{run.outcomes?.map((o,i)=><p key={i}>{o.period_start} {t("to_3")} {o.period_end}: {o.qualified_enquiries} {t("qualified_enquiries_2")}</p>)}</section>}
    <section className="panel stage-panel" id="guided-approval" hidden={!!run.internal_workflow?.active||(stage!==2&&!(stage===1&&qualityCorrectionAvailable))}><h2>{t("review")}</h2>{qualityCorrectionAvailable&&<p>{t("correction_request_help")}</p>}<p className="hint">{account.finalHelp}</p><label>{t("revise")}<select value={target} onChange={e=>setTarget(e.target.value)}><option value="content_agent">{t("content_step")}</option><option value="briefing_agent">{t("briefing_step")}</option></select></label><label>{t("specific_feedback")}<textarea maxLength={3000} rows={5} value={notes} onChange={e=>setNotes(e.target.value)} placeholder={t("explain_what_should_change_and_how_do_not_enter_client_informatio")}/></label><div className="actions"><button disabled={busy||!!activeJob||(run.status!=='seo_check_passed'&&!qualityCorrectionAvailable)||notes.trim().length<10} onClick={()=>operation(()=>review('needs_revision'))}>{t("request_revision")} · May incur a charge</button><button disabled={busy||!['Alex','Sam'].includes(reviewer)||run.status!=='seo_check_passed'||(!!run.combined_version&&(!checks.facts||!checks.graphics||!checks.manual||!!run.release_blockers?.length||run.professional_review?.revision!==run.revision))} onClick={()=>operation(()=>review('approved'))}>{t("approve_and_export_locally")} · Free</button><button className="secondary" disabled={busy||!['Alex','Sam'].includes(reviewer)||run.status!=='seo_check_passed'} onClick={()=>operation(()=>review('rejected'))}>{t("reject")}</button></div><p className="hint">{!canFinalReview?"Only Alex or Sam can give final approval.":run.status!=="seo_check_passed"?"Complete all automated checks first. A blue review error means the review is incomplete; it does not mean your draft is bad.":run.professional_review?.revision!==run.revision?"Ask an authorized tax adviser to attest to this revision, its evidence and infographic.":run.release_blockers?.length?"Resolve the release checklist above before final approval.":"Confirm the factual, infographic and manual-publication checkboxes, then approve and export locally."}</p><p className="hint">{t("approval_creates_a_local_export_website_linkedin_and_google_busin")}</p></section>
    {run.review_history.length>0 && <section className="panel stage-panel" hidden={stage!==2}><h2>{t("review_history")}</h2>{run.review_history.map((e,i)=><p key={i}><strong>{e.reviewer_name}: {statusLabel(e.action)}</strong><br/>{e.feedback_notes}</p>)}</section>}</>}
    {busy && <p role="status">{t("processing_your_request")}</p>}
  </main>;
}
