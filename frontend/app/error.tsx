'use client';
import { t } from '../lib/ui';

import Link from 'next/link';
export default function ErrorPage({reset}:{reset:()=>void}) {
 return <main><section className="panel"><p className="eyebrow">{t("workspace_recovery")}</p><h1>{t("this_page_could_not_be_displayed")}</h1><p>{t("frontend_render_failure")}</p><p>{t("your_previously_saved_runs_remain_on_the_server_a_submitted_task_")}</p><p>{t("reload_the_view_then_inspect_the_saved_task_before_repeating_a_pa")}</p><button onClick={reset}>{t("reload_view")}</button><Link href="/">{t("open_saved_workspace")}</Link></section></main>;
}
