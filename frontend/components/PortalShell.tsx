'use client';
import SessionGuard from './SessionGuard';
import { t } from '../lib/ui';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { portalCopy } from '../lib/ui';
export default function PortalShell({children}:{children:React.ReactNode}){
 const path=usePathname();const [open,setOpen]=useState(false);
 useEffect(()=>{setOpen(false);},[path]);
 useEffect(()=>{function close(e:KeyboardEvent){if(e.key==='Escape')setOpen(false);}window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[]);
 const items=[['/dashboard','Overview','01'],['/',t("content_workspace"),'02'],['/visibility',t("ai_visibility"),'03'],['/system',t("system_status"),'04']];
 return <div className="portal-shell"><a className="skip-link" href="#portal-main">{t("skip_to_workspace")}</a>
  <header className="mobile-header"><Link href="/dashboard">{t("muster_amp_partner")}</Link><button aria-controls="portal-navigation" aria-expanded={open} onClick={()=>setOpen(!open)}>{open?t("close_menu"):'Menu'}</button></header>
  <aside className={'portal-sidebar'+(open?' is-open':'')} id="portal-navigation"><Link href="/dashboard" className="portal-logo" aria-label={t("muster_partner_overview")}><img src="/brand/logo.svg" alt={t("muster_partner")} width={210} height={28}/></Link><p className="sidebar-eyebrow">{t("content_amp_search")}</p><nav aria-label={t("main_navigation")}>{items.map(([href,label,number])=><Link key={href} href={href} aria-current={path===href?'page':undefined}><span aria-hidden="true">{number}</span>{label}</Link>)}</nav><div className="sidebar-footer"><span className="status-dot"/>  {t("local_review_portal")}<p>{portalCopy.contentLanguage}</p><small>{t("publishing_always_stays_manual")}</small></div></aside>
  {open&&<button className="nav-backdrop" aria-label={t("close_navigation")} onClick={()=>setOpen(false)}/>}
  <div className="portal-content" id="portal-main" tabIndex={-1}><div className="portal-topbar"><span>{t("muster_workspace")}</span><span className="language-badge">{t("en_interface")} <span aria-hidden="true">/</span>  {t("de_content")}</span></div><SessionGuard>{children}</SessionGuard><footer className="portal-footer">{t("muster_amp_partner_research_review_and_manual_publishing")}</footer></div>
 </div>;
}
