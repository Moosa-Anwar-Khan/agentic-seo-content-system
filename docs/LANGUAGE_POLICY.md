# Language policy

The operator portal is temporarily **English**. Generated deliverables remain
**German**, using formal **Sie** and Muster's branding rules. This takes
precedence over older German-interface instructions.

| Surface | Language |
| --- | --- |
| Navigation, buttons, forms, account/MFA screens, statuses and validation | English |
| Readiness, failure notices, recovery actions and administrator diagnostics | English |
| Code, developer documentation, CLI summaries and application logs | English |
| Articles, briefings, topic plans, social posts and infographic instructions | German, formal Sie |
| Publishing exports, claim/source quotations and saved editorial findings | German/original source language |
| Original business and branding references | Unchanged original language |
| Schema keys, IDs, URLs, status/error codes and measured provider values | Stable technical values |

## Maintaining the boundary

Centralize interface copy in `frontend/lib/ui.ts` and account/history copy in
`frontend/lib/accounts.ts`. Model instructions must require German prose while
preserving schema keys and technical IDs. Interface translation must not change
workflow transitions, permissions, evidence or approval records.

Existing German API `detail` strings and persisted errors remain compatible.
The portal presents structured English `user_error` / `error_info` guidance;
original German findings remain available for review. Do not rewrite saved
source quotations, legal text, German detection patterns or business references
as part of interface localization. Preserve the **KI-generiert** image marking.

A language instruction does not establish factual accuracy. Automated checks,
qualified professional attestation, final approval and manual publishing remain
required. See [workflow](WORKFLOW.md), [failure diagnostics](FAILURE_DIAGNOSTICS.md)
and verification.
