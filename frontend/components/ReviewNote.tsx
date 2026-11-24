'use client';
import { useState } from 'react';
import { t, systemNote } from '../lib/ui';
/** English operational guidance alongside unchanged German source findings. */
export default function ReviewNote({text}:{text:string}) {
 const [expanded,setExpanded]=useState(false);
 const translated=systemNote(text);
 return <span>{translated??t("a_german_editorial_or_source_requirement_needs_review")} <button type="button" className="note-toggle" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{t("original_german_finding")}</button>{expanded&&<span lang="de" className="original-note">{text}</span>}</span>;
}
