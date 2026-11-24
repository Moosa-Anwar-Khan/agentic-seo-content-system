from io import BytesIO
import random
from zipfile import ZipFile
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from src.api.app import create_app
from tests.conftest import authenticated_headers, own_fixture
from src.orchestrator.workflow import Workflow
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.review import ReviewRequest
from src.services.images import ImageProvider, ImageService, ImageProcessingError, SIZES
from src.services.image_processing import keyword_slug, public_filename, raw_image_path, process_image
from src.schemas.assets import ImageAsset
from src.services.social import SocialService

class FakeImageProvider(ImageProvider):
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def generate(self, prompt, size):
        self.calls.append((prompt, size))
        if self.fail:
            raise TimeoutError('provider timed out')
        image = Image.new('RGB', size, '#F9FAFB')
        data = BytesIO()
        image.save(data, format='PNG')
        return data.getvalue(), None

def test_text_only_and_optional_social_review(settings):
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        assert state.status == 'seo_check_passed'
        assert workflow.storage.list_assets(str(state.run_id), 'post') == []
        posts = SocialService(settings, workflow.storage).prepare(state)
        assert {p.platform for p in posts} == {'linkedin', 'google_business_profile'}
        assert state.executed_agents.count('content_agent') == 1
        approved = SocialService(settings, workflow.storage).review(state, str(posts[0].id), 'approved', 'Alex')
        assert approved.status == 'approved'
        with pytest.raises(ValueError):
            SocialService(settings, workflow.storage).review(state, str(posts[0].id), 'rejected', 'Sam')

def test_images_are_independent_versioned_and_badged(settings):
    settings.image_generation_enabled = True
    provider = FakeImageProvider()
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        initial_agents = list(state.executed_agents)
        service = ImageService(settings, workflow.storage, provider)
        for platform, (_, dimensions) in SIZES.items():
            if platform != 'website':
                SocialService(settings, workflow.storage).prepare(state)
            first = service.generate(state, platform)
            second = service.generate(state, platform, str(first.id))
            assert second.version == 2 and second.parent_image_id == first.id
            assert (settings.output_dir / first.file_path).is_file()
            assert (settings.output_dir / second.file_path).is_file()
            with Image.open(settings.output_dir / second.file_path) as image:
                assert image.size == dimensions
                assert image.format == ('WEBP' if platform != 'google_business_profile' else 'JPEG')
                assert all(abs(a-b) <= 2 for a,b in zip(image.getpixel((20, 20)), (249, 250, 251)))
                badge_area = image.crop((dimensions[0]-210,dimensions[1]-65,dimensions[0],dimensions[1]))
                assert sum(1 for r,g,b in zip(*(iter(badge_area.tobytes()),)*3) if r<100 and g<150 and b<180) > 100
            assert second.seo_filename.endswith('-v2.' + ('webp' if platform != 'google_business_profile' else 'jpg'))
            assert second.original_format == 'png'
            assert second.focus_keyword == state.briefing.primary_keyword
            assert second.original_dimensions == SIZES[platform][0]
            assert second.final_dimensions == dimensions
            assert second.original_file_size and second.optimized_file_size
            assert second.size_reduction_percent is not None
            assert second.optimization_status == 'completed'
            assert not raw_image_path(settings, second).exists()
            assert service.review(state, str(second.id), 'approved', 'Sam').status == 'approved'
        assert workflow.get(str(state.run_id)).executed_agents == initial_agents
        assert len(provider.calls) == 6

def test_provider_failure_and_attempt_limit_leave_text_intact(settings):
    settings.image_generation_enabled = True
    settings.max_image_attempts_per_run = 1
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        service = ImageService(settings, workflow.storage, FakeImageProvider(fail=True))
        with pytest.raises(RuntimeError):
            service.generate(state, 'website')
        assert workflow.get(str(state.run_id)).status == 'seo_check_passed'
        with pytest.raises(RuntimeError):
            service.generate(state, 'website')

def test_stale_assets_and_authenticated_api(settings):
    settings.image_generation_enabled = True
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        post = SocialService(settings, workflow.storage).prepare(state)[0]
        image = ImageService(settings, workflow.storage, FakeImageProvider()).generate(state, 'website')
        revised = workflow.submit_review(str(state.run_id), ReviewRequest(action='needs_revision',
            feedback_notes='Bitte ergänzen Sie eine Checkliste für das Erstgespräch.',
            expected_revision=state.revision), 'Alex')
        with pytest.raises(ValueError):
            SocialService(settings, workflow.storage).review(revised, str(post.id), 'approved', 'Alex')
        with pytest.raises(ValueError):
            ImageService(settings, workflow.storage, FakeImageProvider()).review(revised, str(image.id), 'approved', 'Alex')
    with TestClient(create_app(settings)) as client:
        path = f'/runs/{state.run_id}/images/{image.id}/file'
        assert client.get(path).status_code == 401
        headers = authenticated_headers(client)
        own_fixture(client,state)
        assert client.get(path, headers=headers).status_code == 200
        assert client.post(f'/runs/{state.run_id}/images', headers=headers, json={'platform':'unknown'}).status_code == 422

def test_approved_assets_join_bundle_after_website_export(settings):
    settings.image_generation_enabled = True
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        approved = workflow.submit_review(str(state.run_id), ReviewRequest(action='approved', expected_revision=0),
            'Alex')
        post = SocialService(settings, workflow.storage).prepare(approved)[0]
        SocialService(settings, workflow.storage).review(approved, str(post.id), 'approved', 'Alex')
        image = ImageService(settings, workflow.storage, FakeImageProvider()).generate(approved, 'website')
        ImageService(settings, workflow.storage, FakeImageProvider()).review(approved, str(image.id), 'approved', 'Sam')
    with TestClient(create_app(settings)) as client:
        headers = authenticated_headers(client)
        own_fixture(client,state)
        response = client.get(f'/runs/{state.run_id}/bundle', headers=headers)
        assert response.status_code == 200
        with ZipFile(BytesIO(response.content)) as archive:
            assert f'social/{post.platform}-{post.id}.json' in archive.namelist()
            assert f'images/{image.seo_filename}' in archive.namelist()

def test_image_api_generation_variation_rejection_and_provider_error(settings, monkeypatch):
    settings.image_generation_enabled = True
    settings.image_api_key = 'fake-test-key'
    fake = FakeImageProvider()
    monkeypatch.setattr('src.services.images.OpenAIImageProvider.generate', lambda self, prompt, size: fake.generate(prompt, size))
    with TestClient(create_app(settings)) as client:
        headers = authenticated_headers(client)
        run = client.post('/runs', headers=headers, json={'icp_id':'icp_1_buy_and_hold'}).json()
        base = f'/runs/{run["run_id"]}/images'
        first_response = client.post(base, headers=headers, json={'platform':'website'})
        assert first_response.status_code == 200
        first = first_response.json()
        file_response = client.get(f'{base}/{first["id"]}/file', headers=headers)
        assert file_response.status_code == 200
        assert file_response.headers['content-type'] == 'image/webp'
        assert first['seo_filename'] in file_response.headers['content-disposition']
        variant = client.post(f'{base}/{first["id"]}/variations', headers=headers).json()
        assert variant['version'] == 2 and variant['parent_image_id'] == first['id']
        rejected = client.post(f'{base}/{variant["id"]}/reviews', headers=headers, json={'action':'rejected'})
        assert rejected.status_code == 200 and rejected.json()['status'] == 'rejected'
        assert client.post(f'{base}/{variant["id"]}/reviews', headers=headers, json={'action':'approved'}).status_code == 409
        fake.fail = True
        assert client.post(base, headers=headers, json={'platform':'website'}).status_code == 502
        assert client.get(f'/runs/{run["run_id"]}', headers=headers).json()['status'] == 'seo_check_passed'

@pytest.mark.parametrize(('value', 'expected'), [
    ('Steuerberater für Immobilieninvestoren', 'steuerberater-fuer-immobilieninvestoren'),
    (' Ä Ö Ü ß  -- Test! ', 'ae-oe-ue-ss-test'),
    ('  Kosten---&  Steuern???', 'kosten-steuern'),
    ('', ''),
])
def test_keyword_slug(value, expected):
    assert keyword_slug(value) == expected

def test_platform_and_version_filenames():
    keyword = 'Steuerberater für Immobilieninvestoren'
    assert public_filename(keyword, 'website', 1, 'webp') == 'steuerberater-fuer-immobilieninvestoren.webp'
    assert public_filename(keyword, 'linkedin', 2, 'webp') == 'steuerberater-fuer-immobilieninvestoren-linkedin-v2.webp'
    assert public_filename(keyword, 'google_business_profile', 1, 'jpeg') == 'steuerberater-fuer-immobilieninvestoren-google-business-profile.jpg'
    assert public_filename(keyword, 'google_business_profile', 2, 'png') == 'steuerberater-fuer-immobilieninvestoren-google-business-profile-v2.png'

class TransparentImageProvider(FakeImageProvider):
    def generate(self, prompt, size):
        self.calls.append((prompt, size))
        image = Image.new('RGBA', size, (20, 60, 130, 0))
        data = BytesIO()
        image.save(data, format='PNG')
        return data.getvalue(), None

def test_gbp_png_for_transparency_or_explicit_graphic(settings):
    settings.image_generation_enabled = True
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        SocialService(settings, workflow.storage).prepare(state)
        transparent = ImageService(settings, workflow.storage, TransparentImageProvider()).generate(state, 'google_business_profile')
        assert transparent.final_format == 'png' and transparent.seo_filename.endswith('.png')
        with Image.open(settings.output_dir / transparent.file_path) as image:
            assert image.format == 'PNG' and image.mode == 'RGBA'
            assert image.getpixel((0, 0))[3] == 0
        graphic = ImageService(settings, workflow.storage, FakeImageProvider()).generate(state, 'google_business_profile', png_requested=True)
        assert graphic.final_format == 'png' and graphic.version == 2
        assert (settings.output_dir / transparent.file_path).is_file()
        with pytest.raises(ValueError):
            ImageService(settings, workflow.storage, FakeImageProvider()).generate(state, 'website', png_requested=True)

def test_focus_keyword_fallback_and_compression(settings):
    settings.image_generation_enabled = True
    class NoisyProvider(FakeImageProvider):
        def generate(self, prompt, size):
            self.calls.append((prompt, size))
            image = Image.frombytes('RGB', size, random.Random(7).randbytes(size[0]*size[1]*3))
            data = BytesIO()
            image.save(data, format='PNG')
            return data.getvalue(), None
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        state.briefing.primary_keyword = '!!!'
        image = ImageService(settings, workflow.storage, NoisyProvider()).generate(state, 'website')
        assert image.focus_keyword_fallback is True
        assert image.seo_filename.startswith(keyword_slug(state.article.title))
        assert image.optimized_file_size < image.original_file_size
        assert image.size_reduction_percent > 0

def test_failed_processing_retries_original_without_provider_or_text_calls(settings, monkeypatch):
    settings.image_generation_enabled = True
    provider = FakeImageProvider()
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        agents_before = list(state.executed_agents)
        service = ImageService(settings, workflow.storage, provider)
        original_processor = process_image
        monkeypatch.setattr('src.services.images.process_image', lambda *args: (_ for _ in ()).throw(ValueError('test processing failure')))
        with pytest.raises(ImageProcessingError) as failure:
            service.generate(state, 'website')
        failed = workflow.storage.get_asset(str(failure.value.image_id), 'image')
        assert failed.optimization_status == 'failed'
        assert raw_image_path(settings, failed).is_file()
        with pytest.raises(ValueError):
            service.review(state, str(failed.id), 'approved', 'Alex')
        monkeypatch.setattr('src.services.images.process_image', original_processor)
        recovered = service.retry_processing(state, str(failed.id))
        assert recovered.optimization_status == 'completed'
        assert not raw_image_path(settings, recovered).exists()
        assert len(provider.calls) == 1
        assert workflow.get(str(state.run_id)).executed_agents == agents_before

def test_legacy_jpg_remains_available(settings):
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        asset = ImageAsset(run_id=state.run_id, content_id=f'{state.run_id}:0:website',
            source_revision=0, icp_id=state.icp.id, platform='website', prompt='Legacy image',
            provider='openai', model='legacy', version=1, status='approved', file_path='legacy')
        path = settings.output_dir / 'runs' / str(state.run_id) / 'images' / f'{asset.id}.jpg'
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (64, 64), 'white').save(path, format='JPEG')
        workflow.storage.save_asset(asset)
    with TestClient(create_app(settings)) as client:
        headers = authenticated_headers(client)
        own_fixture(client,state)
        response = client.get(f'/runs/{state.run_id}/images/{asset.id}/file', headers=headers)
        assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'

def test_api_retry_processing_does_not_generate_again(settings, monkeypatch):
    settings.image_generation_enabled = True
    settings.image_api_key = 'fake-test-key'
    settings.max_image_attempts_per_run = 1
    provider = FakeImageProvider()
    monkeypatch.setattr('src.services.images.OpenAIImageProvider.generate', lambda self, prompt, size: provider.generate(prompt, size))
    original_processor = process_image
    monkeypatch.setattr('src.services.images.process_image', lambda *args: (_ for _ in ()).throw(ValueError('test processing failure')))
    with TestClient(create_app(settings)) as client:
        headers = authenticated_headers(client)
        run = client.post('/runs', headers=headers, json={'icp_id':'icp_1_buy_and_hold'}).json()
        base = f'/runs/{run["run_id"]}/images'
        failed = client.post(base, headers=headers, json={'platform':'website'})
        assert failed.status_code == 502 and failed.json()['image_id']
        image_id = failed.json()['image_id']
        assert client.post(f'{base}/{image_id}/reviews', headers=headers, json={'action':'approved'}).status_code == 409
        assert client.get(f'{base}/{image_id}/file', headers=headers).status_code == 409
        assert client.post(f'{base}/{image_id}/process',headers={'Authorization':'Bearer old-token'}).status_code == 401
        monkeypatch.setattr('src.services.images.process_image', original_processor)
        assert client.post(f'{base}/{image_id}/process', headers=headers).status_code == 200
        assert len(provider.calls) == 1
        assert client.post(base, headers=headers, json={'platform':'website','png_requested':True}).status_code == 422
