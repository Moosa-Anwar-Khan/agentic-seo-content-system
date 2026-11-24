import logging
import httpx
from uuid import uuid4
from threading import Event, Thread
from src.config.settings import Settings
from src.orchestrator.workflow import Workflow
from src.schemas.jobs import Job
from src.schemas.review import ReviewEvent
from src.schemas.decisions import SelectionEvent
from src.services.image_sets import ImageSetService
from src.services.images import ImageService, ImageGenerationError, ImageProcessingError
from src.services.recovery import UncertainOperation, running_job
from src.services.social import SocialService
from src.services.llm import ModelRequestError
from src.services.model_routes import ModelConfigurationError
from src.storage.sqlite_storage import BudgetExceeded,ResearchBudgetExceeded
from src.integrations.dataforseo import SourceRequestError
from src.integrations.wordpress import WordPressResponseError
from src.integrations.official_research import ResearchProviderError
from src.security.run_access import AccessRevoked
from src.services.quality import QualityReviewError, QualityCheckFailed
from src.combined.research import CompetitorResearchError
from src.security.sanitize import PrivacyError, InputSizeError


from src.services.preflight import PreflightBlocked

ERROR_MESSAGES = {
    'preflight_blocked': 'Required saved inputs or configuration are missing. The next model request was not sent. Open Current progress for the specific corrective actions.',
    'quality_repetition': 'The AI reviewer repeated its findings. The incomplete review was rejected; completed review units and the draft remain saved. No automatic paid retry will occur.',
    'input_size': 'The model input exceeds a local safety limit. The affected model request was not sent; saved research remains available.',
    'input_privacy': 'The input privacy check stopped this task before the affected content was sent to a model. Saved results remain available.',
    'competitor_evidence': 'No usable competitor evidence remains after retrieval and input checks. Saved search results remain available; no replacement evidence was invented.',
    'access_revoked': 'Account activation or run access has been revoked. Saved results remain available to authorized accounts.',
    'research_budget':'Das Lauf- oder Monatsbudget ist ausgeschöpft oder reserviert. Die nächste Anfrage wurde nicht gesendet; gespeicherte Ergebnisse bleiben erhalten.',
    'official_research':'Amtliche Quellen konnten nicht vollständig recherchiert werden. Bitte API-Schlüssel, bestätigte Anfragekosten, Quellenliste und gespeicherte Ergebnisse prüfen.',
    'quality_evidence': 'Die KI-Prüfung nennt keine belegten Textstellen im aktuellen Entwurf. Es wurde keine automatische Überarbeitung gestartet. Bitte prüfen Sie die gespeicherte Antwort.',
    'quality_failed': 'Die Qualitätsprüfung hat den Entwurf nicht freigegeben. Der Entwurf ist gespeichert. Sie können die Prüfdetails ansehen und die Prüfungen gezielt erneut ausführen.',
    'output_limit': 'Die KI-Antwort hat das Ausgabelimit erreicht und ist unvollständig. Sie wurde nicht übernommen. Eine neue Generierung kann Kosten verursachen.',
    'unfinished_response': 'Die KI hat die Antwort nicht vollständig abgeschlossen. Bitte prüfen Sie die Modellkonfiguration vor einer kostenpflichtigen Wiederholung.',
    'empty_response': 'Die KI hat keinen verwendbaren Antworttext geliefert. Eine neue Generierung kann Kosten verursachen.',
    'invalid_json': 'Die KI-Antwort entspricht nicht dem erforderlichen JSON-Format. Gespeicherte Daten bleiben erhalten.',
    'schema_validation': 'Die KI-Antwort erfüllt die vorgeschriebene Struktur nicht. Sie wurde nicht an den nächsten Schritt übergeben.',
    'authentication': 'Der KI-Anbieter hat die Zugangsdaten abgelehnt. Bitte prüfen Sie den API-Schlüssel und die Modellberechtigung.',
    'rate_limit': 'Der KI-Anbieter hat die Anfrage wegen eines Nutzungslimits abgelehnt. Bitte prüfen Sie Guthaben und Anbieterlimits.',
    'provider_request_invalid': 'Der KI-Anbieter hat die Anfrageparameter abgelehnt. Bitte prüfen Sie Modell und unterstützte Optionen.',
    'source_auth': 'DataForSEO hat die API-Zugangsdaten abgelehnt. Bitte verwenden Sie API-Login und API-Passwort, nicht das Dashboard-Passwort.',
    'source_invalid_response': 'DataForSEO hat kein verwendbares Datenformat geliefert. Gespeicherte Antworten bleiben für eine lokale Prüfung erhalten.',
    'source_api_error': 'DataForSEO hat einen API-Fehler gemeldet. Bitte prüfen Sie die Aufgabenübersicht beim Anbieter.',
    'source_http_error': 'Die DataForSEO-Anfrage wurde abgelehnt. Bitte prüfen Sie den Anbieterstatus und die Konfiguration.',
    'wordpress_response': 'Die öffentliche WordPress-Schnittstelle hat kein unterstütztes Datenformat geliefert. Die Website bleibt unverändert.',
    'run_budget': 'Das Tokenbudget dieses Laufs reicht für den nächsten Schritt nicht aus. Es wurde keine weitere KI-Anfrage gesendet.',
    'image_budget': 'Die erlaubte Anzahl von Bildanfragen ist erreicht. Es wurde kein weiteres Bild angefordert.',
    'configuration': 'Die Modellkonfiguration unterstützt diesen Schritt nicht. Bitte prüfen Sie Modell, Fähigkeiten und Aufgabenoptionen.',
    'uncertain_outcome': 'Der Ausgang einer kostenpflichtigen Anfrage ist unklar. Bitte prüfen Sie den Anbieterstatus. Eine Wiederholung kann erneut Kosten verursachen.',
    'processing': 'Die Verarbeitung ist fehlgeschlagen. Gespeicherte Ergebnisse bleiben erhalten. Bitte prüfen Sie die Konfiguration vor einer Wiederholung.',
}


class JobWorker:
    def __init__(self, settings: Settings, workflow: Workflow):
        self.settings = settings
        self.workflow = workflow
        self.stop = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        self.workflow.storage.recover_jobs()
        self.thread = Thread(target=self._loop, name='review-job-worker', daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join()

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                if not self.process_one():
                    self.stop.wait(0.7)
            except Exception as exc:
                logging.getLogger(__name__).error('Job worker loop failed (%s)', type(exc).__name__)
                self.stop.wait(1)

    def process_one(self) -> bool:
        job = self.workflow.storage.claim_job()
        if job is None:
            return False
        if job.attempt_id is None:
            job.attempt_id = str(uuid4())
            self.workflow.storage.save_job(job)
        failure_exc = None
        try:
            if job.source_revision is None:
                try:job.source_revision=self.workflow.get(str(job.run_id)).revision
                except KeyError:pass
                self.workflow.storage.save_job(job)
            with running_job(job, self.workflow.storage):
                self._execute(job)
            job.status = 'completed'
            job.error = None
            job.error_code = None
            job.requires_paid_retry = False
            job.failure_summary = None
        except UncertainOperation as exc:
            failure_exc = exc
            job.status = 'paused'
            job.error_code = 'uncertain_outcome'
            job.error = ERROR_MESSAGES[job.error_code]
            job.requires_paid_retry = True
        except Exception as exc:
            failure_exc = exc
            logging.getLogger(__name__).error('Job %s failed (%s)', job.id, type(exc).__name__)
            job.status = 'failed'
            code = ('authentication' if isinstance(exc,httpx.HTTPStatusError) and exc.response.status_code in {401,403} else 'rate_limit' if isinstance(exc,httpx.HTTPStatusError) and exc.response.status_code==429 else 'source_http_error' if isinstance(exc,httpx.HTTPStatusError) else 'connection' if isinstance(exc,(httpx.TimeoutException,httpx.NetworkError)) else 'image_generation' if isinstance(exc,ImageGenerationError) else 'image_processing' if isinstance(exc,ImageProcessingError) else 'preflight_blocked' if isinstance(exc,PreflightBlocked) else 'input_size' if isinstance(exc, InputSizeError) else 'input_privacy' if isinstance(exc, PrivacyError) else 'competitor_evidence' if isinstance(exc, CompetitorResearchError) else 'access_revoked' if isinstance(exc, AccessRevoked) else 'research_budget' if isinstance(exc,ResearchBudgetExceeded) else 'official_research' if isinstance(exc,ResearchProviderError) else exc.code if isinstance(exc, (ModelRequestError, SourceRequestError, QualityReviewError, QualityCheckFailed)) else
                    'configuration' if isinstance(exc, ModelConfigurationError) else
                    ('image_budget' if 'image' in job.kind else 'run_budget') if isinstance(exc, BudgetExceeded) else
                    'wordpress_response' if isinstance(exc, WordPressResponseError) else 'processing')
            job.error_code = code
            job.error = ERROR_MESSAGES.get(code, ERROR_MESSAGES['processing'])
            if isinstance(exc, ModelRequestError):
                job.error = f'{exc.route.task} ({exc.route.provider}/{exc.route.model}): ' + job.error
                if exc.validation_fields:
                    job.error += ' Betroffene Schemafelder: ' + ', '.join(exc.validation_fields) + '.'
            job.requires_paid_retry = code in {'output_limit', 'unfinished_response', 'empty_response',
                                               'invalid_json', 'schema_validation', 'rate_limit',
                                               'authentication', 'provider_request_invalid',
                                               'source_auth', 'source_api_error', 'source_http_error',
                                               'source_invalid_response', 'quality_evidence', 'quality_failed'}
        if failure_exc is not None:
            from src.services.failures import capture
            state = self.workflow.storage.get_run_state(str(job.run_id))
            capture(self.workflow.storage, code=job.error_code, exc=failure_exc, job=job, state=state)
        try:job.result_revision=self.workflow.get(str(job.run_id)).revision
        except KeyError:pass
        self.workflow.storage.save_job(job)
        # A provider failure must not leave persisted token usage at the last
        # successful graph node. Keep the authoritative checkpoint and draft intact.
        try:
            with self.workflow.storage.run_lock(str(job.run_id)):
                state=self.workflow.get(str(job.run_id))
                state.updated_at=max(state.updated_at,job.updated_at)
                self.workflow.storage.save_run_state(state)
        except KeyError:
            pass
        return True

    def _execute(self, job: Job) -> None:
        run_id = str(job.run_id)
        from src.security.run_access import authorize_job
        authorize_job(job, self.workflow.storage)
        if job.kind == 'start':
            state=self.workflow.get(run_id)
            if state.research_scope and not self.settings.demo_mode:
                from src.services.research import ResearchService
                result=ResearchService(self.workflow).execute(run_id)
            else:result = self.workflow.continue_queued(run_id)
            if result.status == 'failed':
                raise QualityCheckFailed('Workflow ended with a failed quality check')
        elif job.kind in {'research_refresh','research_repair'}:
            from src.services.research import ResearchService
            ResearchService(self.workflow).execute(run_id)
        elif job.kind == 'selection':
            result = self.workflow.select_queued(run_id, SelectionEvent.model_validate(job.payload['event']))
            if result.status == 'failed':
                raise QualityCheckFailed('Selected draft ended with a failed quality check')
        elif job.kind == 'review':
            result = self.workflow.review_queued(run_id, ReviewEvent.model_validate(job.payload['event']))
            if result.status == 'failed':
                raise QualityCheckFailed('Workflow ended with a failed quality check')
        elif job.kind == 'quality_checks':
            from src.services.guided_qa import readiness
            qa_ready=readiness(self.workflow.storage,self.workflow.get(run_id),self.settings)
            if not qa_ready['ready'] and not qa_ready.get('token_budget',{}).get('ready',True):
                raise BudgetExceeded('Run token ceiling cannot fit a pending review request; no request was sent')
            if not qa_ready['ready']:
                raise QualityReviewError('unchanged_review_blocked')
            result = self.workflow.quality_checks_queued(run_id, job.payload['source_revision'])
            if result.status == 'failed':
                raise QualityCheckFailed('Retained draft did not pass quality checks')
        elif job.kind == 'evaluation':
            from src.services.evaluation import execute
            execute(self.workflow,job)
        elif job.kind == 'evaluation_images':
            from src.services.evaluation_images import execute
            execute(self.workflow,job)
        elif job.kind == 'ai_visibility':
            from src.services.visibility import collect
            collect(self.workflow,job)
        elif job.kind == 'social':
            job.current_step = 'social_content'
            self.workflow.storage.save_job(job)
            with self.workflow.storage.run_lock(run_id):
                state = self.workflow.get(run_id)
                if 'source_revision' in job.payload and state.revision != job.payload['source_revision']:
                    raise ValueError('Content revision changed before social generation')
                SocialService(self.settings, self.workflow.storage).prepare(state, job.id)
        elif job.kind in {'image_set', 'image_set_variation'}:
            job.current_step = 'image_generation'
            self.workflow.storage.save_job(job)
            with self.workflow.storage.run_lock(run_id):
                state = self.workflow.get(run_id)
                if 'source_revision' in job.payload and state.revision != job.payload['source_revision']:
                    raise ValueError('Content revision changed before image generation')
                result = ImageSetService(self.settings, self.workflow.storage).create(
                    state, job.payload.get('png_requested', False),
                    job.payload.get('parent_id'), job.id)
            failed=next((item for item in result['variants'] if item.optimization_status != 'completed'),None)
            if failed:raise ImageProcessingError(failed.id)
        elif job.kind == 'single_image_variation':
            job.current_step = 'image_generation'
            self.workflow.storage.save_job(job)
            with self.workflow.storage.run_lock(run_id):
                state = self.workflow.get(run_id)
                if 'source_revision' in job.payload and state.revision != job.payload['source_revision']:
                    raise ValueError('Content revision changed before image generation')
                parent = self.workflow.storage.get_asset(job.payload['parent_id'], 'image')
                if not parent or parent.run_id != job.run_id:
                    raise ValueError('Image version is unavailable')
                ImageService(self.settings, self.workflow.storage).generate(
                    state, parent.platform, str(parent.id), parent.png_requested, job.id)
        else:
            raise ValueError('Unknown job kind')
