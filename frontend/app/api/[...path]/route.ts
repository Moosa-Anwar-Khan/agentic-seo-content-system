import { NextRequest } from 'next/server';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
async function proxy(request: NextRequest, context: {params:Promise<{path:string[]}>}) {
  const {path} = await context.params;
  const route = '/' + path.join('/');
  const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
  const allowed = new RegExp('^/(icps|runs|runs/'+uuid+'(/(sharing|owner|release|graphic|selection|reviews|editorial|article|topic-map|bundle|ai-calls|social-posts(?:/'+uuid+'/reviews)?|image-sets(?:/'+uuid+'/(?:variations|process/(?:website|linkedin|google_business_profile)))?|images(?:/'+uuid+'/(?:file|reviews|variations|process))?))?)$');
  const diagnosticsAllowed = new RegExp('^/jobs/'+uuid+'/diagnostics$').test(route) && request.method==='GET';
  const evaluationAllowed = new RegExp('^/runs/'+uuid+'/evaluation(?:/(?:preflight|download|infographic|assistant-review|combined-review|review-package|test-walkthrough|test-export|images/(?:preflight|website|linkedin|google_business_profile)))?$').test(route) && request.method==='GET';
  const internalAllowed = new RegExp('^/runs/'+uuid+'/internal-workflow(?:/download)?$').test(route) && (request.method==='GET'||request.method==='POST'&&!route.endsWith('/download'));
  const jobsAllowed = new RegExp('^/jobs(?:/'+uuid+'(?:/retry)?)?$').test(route);
  const imageOptionsAllowed = (['/image-generation/options','/system/readiness'].includes(route) || new RegExp('^/runs/'+uuid+'/(progress|preflight|diagnostics|review-preview)$').test(route)) && request.method === 'GET';
  const visibilityAllowed = new RegExp('^/runs/'+uuid+'/(?:research-evidence|visibility(?:/observations)?)$').test(route) && (request.method==='GET' || route.endsWith('/observations')&&request.method==='POST');
  const reportsAllowed = new RegExp('^/runs/'+uuid+'/(?:dashboard|analysis-report\\.pdf)$').test(route) && request.method==='GET';
  const sessionAllowed = route === '/session' || route === '/session/logout';
  const authAllowed = ['/auth/register','/auth/bootstrap','/auth/bootstrap/status','/auth/login','/auth/verify','/auth/setup/password','/auth/setup/mfa','/auth/setup/confirm'].includes(route);
  const adminAllowed = new RegExp('^/admin/users(?:/'+uuid+'/(?:reset|disable|enable|approve|reject))?$').test(route);
  const recommendedAllowed = new RegExp('^/(?:budget|runs/'+uuid+'/(?:professional-reviews|publication|outcomes|social-posts/'+uuid+'/article-link))$').test(route);
  if (!allowed.test(route) && !internalAllowed && !evaluationAllowed && !visibilityAllowed && !imageOptionsAllowed && !reportsAllowed && !jobsAllowed && !diagnosticsAllowed && !sessionAllowed && !authAllowed && !adminAllowed && !recommendedAllowed) return Response.json({detail:'Route nicht erlaubt'},{status:404});
  const origin=request.headers.get('origin');
  let sameOrigin=false;
  try {
    const parsed=new URL(origin ?? '');
    sameOrigin=['http:','https:'].includes(parsed.protocol) && parsed.host===request.headers.get('host');
  } catch { /* Invalid or missing origins are rejected for state changes. */ }
  if (request.method === 'POST' && !sameOrigin) {
    return Response.json({detail:'Ungültiger Anfrageursprung'},{status:403});
  }
  const session=request.cookies.get('review_session')?.value;
  const csrf=request.cookies.get('review_csrf')?.value;
  if (request.headers.get('authorization')) return Response.json({detail:'Bitte anmelden.'},{status:401});
  if (!session && !authAllowed) {
    return Response.json({detail:'Bitte anmelden.'},{status:401});
  }
  const base=process.env.API_BASE_URL ?? 'http://127.0.0.1:8000';
  // No user-controlled upstream URL or browser-visible server credentials.
  try {
    const body=request.method==='POST' ? await request.text() : undefined;
    if (body && body.length>(route.endsWith('/graphic')?410000:route.endsWith('/editorial')?200000:route.endsWith('/observations')?16000:8000)) return Response.json({detail:'Anfrage zu groß'},{status:413});
    const upstreamHeaders:Record<string,string>={'Content-Type':'application/json'};
    if(origin) upstreamHeaders.Origin=origin;
    if(session) upstreamHeaders.Cookie='review_session='+session+(csrf?'; review_csrf='+csrf:'');
    if(request.method==='POST' && request.headers.get('x-csrf-token')) upstreamHeaders['X-CSRF-Token']=request.headers.get('x-csrf-token')!;
    const query=visibilityAllowed||reportsAllowed||route==='/runs'||(route.endsWith('/preflight')||route.endsWith('/review-preview')||route.endsWith('/test-export'))?request.nextUrl.search:'';
    const response=await fetch(base+route+query,{method:request.method,headers:upstreamHeaders,body,cache:'no-store',signal:AbortSignal.timeout(600000)});
    const headers=new Headers({'Content-Type':response.headers.get('content-type') ?? 'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
    if(route.endsWith('/review-preview')||route.endsWith('/combined-review')||route.endsWith('/review-package')){const csp=response.headers.get('content-security-policy');if(csp)headers.set('Content-Security-Policy',csp);}
    const disposition=response.headers.get('content-disposition');
    if(disposition) headers.set('Content-Disposition',disposition);
    for (const cookie of response.headers.getSetCookie()) headers.append('Set-Cookie',cookie);
    return new Response(response.body,{status:response.status,headers});
  } catch {
    return Response.json({detail:'Backend nicht erreichbar oder Zeitlimit erreicht. Status vor erneutem Start prüfen.'},{status:502});
  }
}
export const GET=proxy;
export const POST=proxy;
