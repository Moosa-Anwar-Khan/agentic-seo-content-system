import { t } from '../lib/ui';
import Link from 'next/link';
export default function NotFound(){return <main><section className="empty-state"><p className="eyebrow">{t("page_unavailable")}</p><h1>{t("we_could_not_find_this_page")}</h1><p>{t("open_the_workspace_to_find_your_saved_runs_or_check_the_system_st")}</p><Link href="/">{t("open_workspace")}</Link></section></main>;}
