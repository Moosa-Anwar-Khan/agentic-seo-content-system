"""English portal guidance; persisted provider errors and German exports stay unchanged."""
from uuid import uuid4

# code: title, explanation, next action, retry policy
CATALOG = {
 'graphic_input': ('Infographic upload was not accepted', 'The upload did not satisfy the image or current-revision requirements.', 'Check the current revision and QA status. Upload a valid WebP, 1200–1600 px wide, at most 3200 px high and strictly under 300,000 bytes.', 'correct_input'),
 'image_processing': ('Saved image needs local processing', 'The generated image is saved, but a channel variant could not be processed locally.', 'Retry local image processing for the affected asset. Do not buy another generated master to fix a processing error.', 'local_only'),
 'input_size': ('The source input is too large', 'A local safety limit stopped the affected model request before it was sent. Earlier research requests may have incurred charges; saved work remains available.', 'Ask the administrator to check the bounded research handoff. Refresh the saved task after the fix, then explicitly retry if you want to continue.', 'may_charge'),
 'input_privacy': ('The input privacy check stopped this task', 'Some supplied text contains suspected personal data. The affected content was not sent to the AI provider. Earlier requests may already have incurred charges; saved results remain available.', 'Ask the administrator to inspect the input source without sharing its private contents. Remove or exclude unsafe material before explicitly retrying.', 'may_charge'),
 'competitor_evidence': ('No usable competitor pages are available', 'Competitor pages could not be read or were excluded by the input safety checks. Research stopped instead of inventing evidence. Saved search results remain available.', 'Review the saved research coverage and excluded pages. Ask the administrator to resolve access or input issues before explicitly retrying; earlier requests may already have incurred charges.', 'may_charge'),
 'pending_account': ('Administrator approval required', 'Your account is registered and awaiting administrator activation. Pending accounts cannot view runs or use credits.', 'Ask your administrator to approve the registration. Then sign in and set up your authenticator. No email will be sent.', 'inspect_first'),
 'setup_details': ('Check administrator details', 'The administrator name, email or password does not meet the account requirements.', 'Enter your own name, a complete email address including its domain, and matching passwords of 12–128 characters. For an existing pending registration, use its original password.', 'correct_input'),
 'setup_exists': ('An administrator already exists', 'First-administrator setup has already completed. It cannot create another administrator.', 'Return to sign in with the administrator account. Use trusted terminal recovery if access has been lost. Existing accounts and saved work remain unchanged.', 'sign_in'),
 'setup_pending_password': ('Use your registration password', "This email already has a pending registration. Administrator setup needs that account's original password as well as the valid local setup code.", 'Enter your original registration password in both password fields and use the current local setup code. Your account has not been changed. If you cannot recover that password, use a different administrator email.', 'correct_input'),
 'setup_account': ('Check administrator account details', 'The setup request conflicts with an existing account or reserved reviewer identity.', 'If you already registered, use that same email and its original registration password with a fresh local setup code. Active or rejected accounts cannot be claimed through this form. Use your own name, or trusted terminal recovery when appropriate.', 'correct_input'),
 'setup_code': ('Check administrator setup', 'The local setup code is invalid, expired, or setup has already completed.', 'Use the code printed in the local backend terminal. It expires after 15 minutes. Restart the backend for a new code, or sign in if an administrator already exists. Attempts are limited.', 'correct_input'),
 'registration': ('Check account details', 'Registration could not be completed with these details.', 'Use a valid email, matching passwords of 12–128 characters and a name other than Alex or Sam. If already registered, contact the administrator for activation or recovery.', 'correct_input'),
 'access_revoked': ('Run access has changed', 'The original account is disabled or no longer has access to this run. Further queued requests have been stopped.', 'Ask the owner or administrator to review activation and sharing. Saved results remain with the run. Retrying after access is restored may incur charges.', 'inspect_first'),
 'connection': ('Connection interrupted', 'The portal cannot reach the local server. The result of a submitted request may be unknown.', 'Check that the backend is running, reconnect, then inspect the saved task before submitting again.', 'inspect_first'),
 'session': ('Please sign in again', 'Your session has expired or you are not signed in.', 'Sign in with your email, password and authenticator code, then reopen your saved run.', 'sign_in'),
 'permission': ('This action is not available to your account', 'Your account cannot perform this action, or the security check has expired.', 'Refresh the page. If the action is still blocked, ask the administrator to check your role.', 'inspect_first'),
 'validation': ('Check your input', 'One or more fields do not meet the requirements.', 'Check the highlighted fields, file format and size, then submit again.', 'correct_input'),
 'conflict': ('This step is not ready yet', 'The saved revision may have changed, or a required approval, source or task is still pending.', 'Refresh the saved run and review the stage requirements before trying again.', 'inspect_first'),
 'not_found': ('Saved item not found', 'The requested run or file is no longer available at this address.', 'Return to the workspace and select a saved run. Keep any downloaded copies.', 'inspect_first'),
 'research_budget': ('The spending limit has been reached', 'The next request was blocked because the run or monthly budget is spent or reserved.', 'Check recorded and reserved costs. Ask the administrator to review limits and uncertain provider charges.', 'inspect_first'),
 'run_budget': ('The token limit has been reached', 'The next AI step could not fit within the run token budget.', 'Review the saved draft and token settings before deciding whether to continue.', 'inspect_first'),
 'image_budget': ('No more image requests are available', 'The image attempt limit or spending allowance prevents another generation.', 'Review existing images and the run budget before requesting another paid attempt.', 'inspect_first'),
 'configuration': ('This step needs configuration', 'The selected model, credentials or supported options are not configured correctly.', 'Open System status. Ask the administrator to correct the relevant model route or API key, then explicitly retry.', 'may_charge'),
 'authentication': ('The AI provider rejected access', 'The API key or model access was rejected by the provider.', 'Ask the administrator to check the provider key and model permissions. Do not share the key in feedback.', 'may_charge'),
 'rate_limit': ('The provider has reached a usage limit', 'The provider is currently refusing requests because of a rate, quota or account limit.', 'Check provider credits and usage limits, wait if necessary, then explicitly retry.', 'may_charge'),
 'provider_request_invalid': ('The model rejected the request settings', 'The selected model does not accept one of the requested options.', 'Ask the administrator to check the model and supported request options.', 'may_charge'),
 'output_limit': ('The AI response was incomplete', 'The response reached its output limit and was not accepted.', 'Review the model output limit and saved task before choosing a paid retry.', 'may_charge'),
 'quality_repetition': ('The AI review repeated itself', 'The repetitive response was rejected. Your draft and completed review units remain saved.', 'Inspect the failed review unit before deciding whether to authorize another paid attempt.', 'may_charge'),
 'unfinished_response': ('The AI response did not finish', 'The provider did not return a completed answer.', 'Inspect the task and provider status before explicitly retrying.', 'may_charge'),
 'empty_response': ('The AI returned no usable text', 'No usable answer was available for the next step.', 'Check provider settings and task status before choosing a paid retry.', 'may_charge'),
 'invalid_json': ('The AI response could not be used', 'The answer did not follow the required structured format.', 'Ask the administrator to review the model route. A new generation may incur a charge.', 'may_charge'),
 'schema_validation': ('The AI response is missing required information', 'The answer did not meet the content structure required by this workflow.', 'Review the saved task and model configuration before explicitly retrying.', 'may_charge'),
 'preflight_blocked': ('Writing prerequisites need attention', 'The next model request was stopped because required saved inputs or configuration are missing. Saved research and drafts remain available.', 'Open Current progress and follow the specific input, linking or configuration instructions. Retrying without correcting the inputs will not help.', 'correct_input'),
 'quality_failed': ('Draft saved — corrections required', 'An article was generated and saved. It has not passed the quality checks and is not approved for publishing. Automatic corrections have stopped.', 'Open Draft & Quality Checks to inspect the specific findings and preview or download the saved article. Request a specific correction only after reviewing the findings. Rechecking alone does not change the article.', 'may_charge'),
 'quality_evidence': ('The quality review lacks supporting evidence', 'The AI review did not identify valid passages in the current draft. No automatic rewrite was started.', 'Review the saved draft and check results before rerunning the quality review.', 'may_charge'),
 'source_auth': ('Research provider access was rejected', 'DataForSEO did not accept the configured credentials.', 'Use the DataForSEO API login and API password, rather than the dashboard password.', 'may_charge'),
 'source_api_error': ('The research provider reported an error', 'Some keyword or search-result evidence could not be retrieved.', 'Review available evidence and provider task history before explicitly retrying.', 'may_charge'),
 'source_http_error': ('The research request was rejected', 'The provider did not accept the research request.', 'Check provider availability and account settings, then inspect the saved task.', 'may_charge'),
 'source_invalid_response': ('The research response could not be read', 'The provider returned an unsupported or incomplete format.', 'Ask the administrator to inspect the saved provider response. Do not paste credentials into feedback.', 'may_charge'),
 'official_research': ('Official source research is incomplete', 'Required official source text could not be retrieved or verified.', 'Review the source list, access settings and research allowance. Unsupported claims must not be approved.', 'may_charge'),
 'wordpress_response': ('Website evidence could not be read', 'The public WordPress response was not in the expected format. The website was not changed.', 'Check public website access and review the saved evidence before retrying.', 'inspect_first'),
 'uncertain_outcome': ('Check the previous request before retrying', 'A paid request may have reached the provider, but its result is not confirmed locally.', 'Inspect provider request history and the saved task. Do not automatically submit another paid request.', 'inspect_first'),
 'processing': ('The action could not be completed', 'The action failed. Previously saved work remains available, but the latest operation needs inspection.', 'Refresh the task status. If it is still unclear, give the administrator the reference below before retrying.', 'inspect_first'),
 'image_generation': ('Image generation failed', 'The text draft remains saved. The image provider did not return a usable result.', 'Check the image model, account access and saved request status before requesting another image. A new attempt may incur charges.', 'inspect_first'),
}


def guidance(code='processing', reference=None):
    code = code if code in CATALOG else 'processing'
    title, message, action, retry = CATALOG[code]
    return {'code': code, 'title': title, 'message': message,
            'next_steps': [action], 'retry': retry, 'reference': reference or uuid4().hex[:12]}


def http_guidance(status, detail=None):
    # Classify only known local messages. Never echo user input or provider bodies.
    code = {401:'session',403:'permission',404:'not_found',409:'conflict',413:'validation',422:'validation',429:'rate_limit',503:'connection',504:'connection'}.get(status, 'processing')
    text = detail if isinstance(detail,str) else ''
    if text.startswith('Your account is awaiting administrator approval'): code = 'pending_account'
    elif text.startswith(('Invalid or expired local setup code', 'Administrator setup is unavailable')): code = 'setup_code'
    elif text.startswith(('Use a valid email', 'These account details cannot', 'Registration could not')): code = 'registration'
    elif text.startswith('Check administrator details.'): code = 'setup_details'
    elif text.startswith('An administrator already exists.'): code = 'setup_exists'
    elif text.startswith('Pending registration found.'): code = 'setup_pending_password'
    elif text.startswith(('This email belongs to an account that cannot', 'This trusted reviewer name')): code = 'setup_account'
    elif text.startswith('The original actor must'): code = 'access_revoked'
    if status == 502 and 'Bild' in text:
        code = 'image_generation'
    if status == 409:
        if any(x in text.lower() for x in ('budget','kostenlimit','bildlimit')): code='research_budget'
        elif any(x in text.lower() for x in ('konfiguriert','eingerichtet','modellkonfiguration')): code='configuration'
    return guidance(code)


# Presentation-only translations. Saved German findings remain authoritative.
_CONSOLE_FINDINGS = {
    'Strukturierter Entwurf fehlt.': 'The structured draft is missing.',
    'Amtliche Quellen fehlen.': 'Official sources are missing. Complete the evidence research before approval.',
    'Quellenabruf unvollständig; Quellenliste fachlich vervollständigen.': 'Official source retrieval is incomplete. A professional must verify the complete source list.',
    'Demo-Inhalte können nicht als Live-Inhalte freigegeben werden.': 'Demo content cannot receive live approval. Start a separate live run when configuration has been verified.',
    'Geprüfte Hauptinfografik für diese Revision fehlt.': 'Upload and review the required infographic for this revision before approval.',
    'Drei echte, freigegebene Bewertungen für L12 fehlen.': 'Historical testimonial requirement. Reviews are optional under the current policy; omit them when unavailable.',
    'Freigegebene Preise fehlen.': 'Approved pricing information is missing. Supply verified business inputs.',
    'Freigegebenes persönliches Statement mit Zuschreibung fehlt.': 'Anna’s approved personal statement and attribution are still required before release.',
    'Freigegebene Gebührenregelung nach Änderung der Preistabelle erneut bestätigen.': 'The fee source changed. Confirm the fee presentation again before release.',
    'Freigegebener StBVV-Wortlaut in Kostenübersicht und Kostenantwort fehlt.': 'Use the approved StBVV fee wording in the costs section and FAQ before release.',
    'Genau eine H1 erforderlich.': 'The article must have exactly one H1 heading.',
    'Hauptkeyword fehlt in der tatsächlichen H1.': 'The primary keyword is missing from the H1.',
    'Informelle Ansprache gefunden.': 'The draft uses informal address. German output must use formal Sie.',
    'Unzulässige Garantie.': 'The draft contains a prohibited guarantee.',
    'Hauptkeyword fehlt im Slug.': 'The primary keyword is missing from the URL slug.',
    'Hauptkeyword fehlt in mindestens zwei H2.': 'At least two H2 headings must include the primary keyword.',
    'Überschriftenebene wurde übersprungen.': 'The draft skips a heading level.',
    'Überschriften benötigen eindeutige Sprungziele.': 'Headings need unique navigation anchors.',
    'Roher HTML-Code im Markdown ist nicht zulässig.': 'Raw HTML is not allowed in the draft Markdown.',
    'Unbekannte Abschnittsmarkierung.': 'A section marker is not recognized.',
    'Blogabschnitte stehen nicht in der vorgegebenen Reihenfolge.': 'Blog sections are in the wrong order.',
    'Individuelles Signature-Element fehlt.': 'The required signature element is missing.',
    'Landingpage benötigt 600-3000 Wörter.': 'The landing page must contain 600–3000 words.',
    'Vier bis sechs Kontakt-CTAs erforderlich.': 'Four to six contact calls to action are required.',
    'L14 benötigt sechs bis acht FAQ-Fragen.': 'Section L14 requires six to eight FAQ questions.',
    'S10 benötigt mindestens zwei Frageüberschriften.': 'Section S10 requires at least two question headings.',
    'S08 benötigt drei bis sieben Kernabschnitte.': 'Section S08 requires three to seven core subsections.',
    'Mindestens drei kontextuelle Bloglinks in S08 erforderlich.': 'Section S08 needs at least three contextual blog links.',
    'Mindestens ein kontextueller Ratgeberlink in S08 erforderlich.': 'Section S08 needs at least one relevant, verified contextual guide link under the starter-library policy.',
    'S15 benötigt ein bis acht vorhandene Ratgeberziele.': 'Section S15 needs one to eight distinct relevant, verified guide destinations under the starter-library policy.',
    'S15 benötigt sechs bis acht vorhandene Blogziele.': 'Section S15 needs six to eight existing blog destinations.',
    'Ein bis drei passende Landingpage-Links erforderlich.': 'One to three relevant landing-page links are required.',
    'Bloglinks nur unter L14 zulässig.': 'Blog links are allowed only in section L14.',
    'Zwei bis vier vorhandene Ratgeberlinks unter L14 erforderlich.': 'Section L14 needs two to four existing guide links.',
    'Fachliche Prüfung hat den Entwurf nicht bestätigt.': 'Professional review did not confirm the draft.',
}


def console_release_blockers(findings):
    """Show English operator guidance without rewriting persisted German evidence."""
    prefixes = {
        'Offene Fachfrage:': 'An unresolved professional question requires qualified review. Inspect the German claim/source sheet and original finding.',
        'Quellenstand erneuern:': 'Refresh the dated official source before approval.',
        'Pflichtabschnitt fehlt:': 'A required content section is missing. Review the saved report and request a correction.',
        'Interner Link nicht im vorhandenen Seiteninventar:': 'An internal link is absent from the verified website inventory.',
        'Externer Link nicht als amtliche Quelle oder Kanal freigegeben:': 'An external link is not an approved official source or channel.',
    }
    business_notes = {
        'Originalbestand': 'Confirm how existing service content outside the approved scope should be retained or revised.',
        'L08': _CONSOLE_FINDINGS['Freigegebenes persönliches Statement mit Zuschreibung fehlt.'],
        'Preise': _CONSOLE_FINDINGS['Freigegebene Preise fehlen.'],
        'Gepflegte Social': 'Confirm active social channels and their URLs before including channel links.',
        'Standort': 'Confirm location, appointment options and the client onboarding process before release.',
        'Infografik': 'Prepare and review the article infographic, then upload it after automated QA passes.',
    }
    def known_business(finding):
        if not finding.startswith('Offene Fachfrage:'): return None
        text=finding.removeprefix('Offene Fachfrage:').strip()
        return next((message for prefix,message in business_notes.items() if text.startswith(prefix)),None)
    return [_CONSOLE_FINDINGS.get(finding) or known_business(finding) or next(
        (message for prefix, message in prefixes.items() if finding.startswith(prefix)),
        'A content requirement needs review. Inspect the original finding in the saved German review report.')
        for finding in findings]
