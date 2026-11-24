'use client';
import { t } from '../lib/ui';

import Link from 'next/link';
import FailureDetails from './FailureDetails';
import { type UserError, localErrors } from '../lib/api';
export default function ErrorNotice({info,onDismiss,admin=false}:{info:UserError|null;onDismiss?:()=>void;admin?:boolean}){
 if(!info)return null;
 return <section className={'notice '+(info.failure?.component==='SEO QA'&&info.failure.category!=='content_corrections'?'notice-review-error':'notice-error')} role="alert"><div className="notice-icon" aria-hidden="true">!</div><div><h2>{info.title}</h2><p>{info.failure?.reason||info.message}</p>{info.failure&&<FailureDetails key={info.failure.reference} failure={info.failure} admin={admin} report={info.diagnostic_report}/>}<ul>{(info.failure?[info.failure.next_action]:info.next_steps).map((step,i)=><li key={i}>{step}</li>)}</ul>{info.retry==='may_charge'&&<p><strong>{t("retrying_can_incur_another_provider_charge_nothing_will_be_retrie")}</strong></p>}{info.retry==='inspect_first'&&<p className="hint">{t("inspect_the_saved_task_first_an_uncertain_paid_request_must_not_b")}</p>}{info.retry==='sign_in'&&<Link href="/">{t("go_to_sign_in")}</Link>}{info.reference&&<small>{t("support_reference")} {info.reference}</small>}{onDismiss&&<button className="secondary" onClick={onDismiss}>{t("dismiss_notice")}</button>}</div></section>;
}
