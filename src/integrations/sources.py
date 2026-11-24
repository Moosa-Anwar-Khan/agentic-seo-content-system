import json
from src.config.settings import Settings
from src.integrations.google_search_console import GoogleSearchConsole
from src.integrations.dataforseo import DataForSEO
from src.integrations.wordpress import WordPressReadOnly
from src.schemas.content import Keyword
from src.security.sanitize import sanitize_payload
from src.services.recovery import current_job, operation_key, begin_paid, UncertainOperation
from src.schemas.decisions import ResearchCoverage


class SourceCollection(tuple):
    """Preserve tuple unpacking while carrying request-local coverage metadata."""
    def __new__(cls, keywords, pages, coverage):
        result = super().__new__(cls, (keywords, pages))
        result.coverage = coverage
        return result

class Sources:
    def __init__(self, settings: Settings):
        self.settings = settings

    def collect(self, seed: str):
        if self.settings.demo_mode:
            # Missing metrics remain null; demo fixtures never impersonate live research.
            payload = json.loads((self.settings.input_dir / "seo-data/demo.json").read_text(encoding="utf-8"))
            return SourceCollection([Keyword.model_validate(k) for k in payload["keywords"]], payload["pages"],
                                    ResearchCoverage(**payload.get('coverage', {})))
        context = current_job.get()
        def cached_read(name, payload, fetch):
            if not context:
                return fetch()
            key = operation_key(name, payload)
            previous = context.storage.get_operation(key)
            if previous and previous['status'] == 'completed':
                return previous['value']['result']
            if previous and previous['status'] == 'pending':
                context.storage.fail_operation(key)
            context.storage.begin_operation(key, str(context.job.run_id))
            try:
                result = fetch()
            except Exception:
                context.storage.fail_operation(key)
                raise
            context.storage.finish_operation(key, {'result': result})
            return result
        def read_gsc():
            client = GoogleSearchConsole(self.settings)
            keywords = client.keywords(seed)
            return {'keywords': [item.model_dump(mode='json') for item in keywords], 'coverage': client.coverage}
        observed = cached_read('gsc-v2', {'seed': seed, 'site': self.settings.gsc_site_url, 'days': self.settings.gsc_days}, read_gsc)
        gsc = [Keyword.model_validate(item) for item in observed['keywords']]
        if context:
            key = operation_key('dataforseo', {'seed': seed, 'location': self.settings.dataforseo_location_code,
                                                 'language': self.settings.dataforseo_language_code})
            previous = context.storage.get_operation(key)
            if previous and previous['status'] == 'completed':
                paid = [Keyword.model_validate(item) for item in previous['value']['keywords']]
            else:
                begin_paid(key, context.storage, str(context.job.run_id))
                try:
                    paid = DataForSEO(self.settings).keywords(seed)
                except UncertainOperation:
                    raise
                except Exception:
                    context.storage.fail_operation(key)
                    raise
                context.storage.finish_operation(key, {'keywords': [item.model_dump(mode='json') for item in paid]})
        else:
            paid = DataForSEO(self.settings).keywords(seed)
        keywords = gsc + paid
        def read_wordpress():
            client = WordPressReadOnly(self.settings)
            pages = client.pages()
            return {'pages': pages, 'coverage': client.coverage}
        inventory = cached_read('wordpress-v2', {'base': self.settings.wordpress_base_url}, read_wordpress)
        pages = inventory['pages']
        if not keywords or not pages:
            raise ValueError("Live mode requires SEO data and public WordPress content; no silent demo fallback")
        coverage = {**observed['coverage'], **inventory['coverage']}
        coverage['limitations'] = observed['coverage'].get('limitations', []) + inventory['coverage'].get('limitations', [])
        if not paid:
            coverage['limitations'].append('DataForSEO hat keine auswertbaren Keyword-Zeilen geliefert. Daraus lässt sich keine fehlende Suchnachfrage ableiten; Vorschläge bleiben vorläufig.')
        return SourceCollection(keywords, sanitize_payload(pages), ResearchCoverage(**coverage))
