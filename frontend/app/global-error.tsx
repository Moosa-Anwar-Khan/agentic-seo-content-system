'use client';
import { t } from '../lib/ui';

export default function GlobalError({reset}:{reset:()=>void}) {
 return <html lang="en"><body style={{fontFamily:'Arial,sans-serif',background:'#f5f7fb',color:'#1b2f4a',padding:32}}><main><h1>{t("the_portal_could_not_be_displayed")}</h1><p>{t("frontend_render_failure")}</p><p>{t("previously_saved_work_remains_on_the_server_check_task_status_bef")}</p><button onClick={reset}>{t("reload_portal")}</button><p><a href="/">{t("return_to_the_workspace")}</a></p></main></body></html>;
}
