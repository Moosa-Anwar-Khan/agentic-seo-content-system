import json
import base64
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from src.api.app import create_app
from tests.conftest import authenticated_headers
from src.orchestrator.workflow import Workflow
from src.schemas.content import CheckResult
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.review import ReviewRequest
from src.services.image_processing import master_image_path
from src.services.image_sets import ImageSetService, ImageMasterError
from src.services.images import ImageProvider, GoogleImageProvider, CompatibleImageProvider
from src.services.llm import ModelRouter
from src.services.model_routes import ModelConfigurationError
from src.services.social import SocialService
from src.storage.sqlite_storage import SQLiteStorage


class MasterProvider(ImageProvider):
    def __init__(self, transparent=False):
        self.calls = []
        self.transparent = transparent

    def generate(self, prompt, size):
        self.calls.append((prompt, size))
        mode = 'RGBA' if self.transparent else 'RGB'
        image = Image.new(mode, size, (230, 238, 246, 0) if self.transparent else '#E6EEF6')
        for x in range(850, 1198):
            for y in range(850, 1198):
                image.putpixel((x, y), (35, 72, 125, 255) if self.transparent else (35, 72, 125))
        output = BytesIO()
        image.save(output, format='PNG')
        return output.getvalue(), None


def test_one_master_produces_three_approved_quality_variants(settings):
    settings.image_generation_enabled = True
    provider = MasterProvider()
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        posts = SocialService(settings, workflow.storage).prepare(state)
        agents = list(state.executed_agents)
        service = ImageSetService(settings, workflow.storage, provider)
        result = service.create(state)
        master, variants = result['master'], result['variants']
        assert len(provider.calls) == 1 and provider.calls[0][1] == (2048, 2048)
        assert master_image_path(settings, master).is_file()
        assert {i.master_id for i in variants} == {master.id}
        assert {i.platform: i.final_dimensions for i in variants} == {
            'website': (1536, 1024), 'linkedin': (1200, 1200),
            'google_business_profile': (720, 720)}
        assert {i.platform: i.final_format for i in variants} == {
            'website': 'webp', 'linkedin': 'webp', 'google_business_profile': 'jpeg'}
        assert {i.platform: i.content_id for i in variants} == {
            'website': f'{state.run_id}:{state.revision}:website',
            **{p.platform: str(p.id) for p in posts}}
        for variant in variants:
            assert variant.optimization_status == 'completed'
            with Image.open(settings.output_dir / variant.file_path) as image:
                assert image.size == variant.final_dimensions
                center = image.getpixel((image.width // 2, image.height // 2))
                assert center[2] > center[0]
                dark = image.crop((image.width-220, image.height-70, image.width, image.height)).convert('RGB')
                assert sum(1 for r,g,b in zip(*(iter(dark.tobytes()),)*3) if r<100 and g<150 and b<180) > 100
            service.image_service.review(state, str(variant.id), 'approved', 'Alex')
        assert workflow.get(str(state.run_id)).executed_agents == agents
        second = service.create(state, parent_id=str(master.id))
        assert len(provider.calls) == 2
        assert second['master'].version == master.version + 1
        assert second['master'].parent_master_id == master.id
        assert all(i.version == second['master'].version for i in second['variants'])
        assert all(i.parent_image_id for i in second['variants'])
        assert master_image_path(settings, master).is_file()
        assert all((settings.output_dir / i.file_path).is_file() for i in variants)


def test_partial_variant_failure_retries_without_provider(settings, monkeypatch):
    settings.image_generation_enabled = True
    provider = MasterProvider(transparent=True)
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        SocialService(settings, workflow.storage).prepare(state)
        agents = list(state.executed_agents)
        service = ImageSetService(settings, workflow.storage, provider)
        from src.services.images import process_image as original
        def fail_linkedin(raw, platform, png_requested, image_settings, allow_upscale=True):
            if platform == 'linkedin':
                raise ValueError('simulated processing failure')
            return original(raw, platform, png_requested, image_settings, allow_upscale)
        monkeypatch.setattr('src.services.images.process_image', fail_linkedin)
        result = service.create(state)
        by_platform = {i.platform: i for i in result['variants']}
        assert by_platform['linkedin'].optimization_status == 'failed'
        assert by_platform['website'].optimization_status == 'completed'
        assert by_platform['google_business_profile'].final_format == 'png'
        assert master_image_path(settings, result['master']).is_file()
        monkeypatch.setattr('src.services.images.process_image', original)
        recovered = service.retry_variant(state, str(result['master'].id), 'linkedin')
        assert recovered.optimization_status == 'completed'
        assert len(provider.calls) == 1
        assert workflow.get(str(state.run_id)).executed_agents == agents


def test_set_api_auth_bundle_and_current_revision(settings, monkeypatch):
    settings.image_generation_enabled = True
    settings.image_api_key = 'fake-test-key'
    provider = MasterProvider()
    monkeypatch.setattr('src.services.images.OpenAIImageProvider.generate',
                        lambda self, prompt, size: provider.generate(prompt, size))
    with TestClient(create_app(settings)) as client:
        token = authenticated_headers(client)
        run = client.post('/runs', headers=token, json={'icp_id': icp_1_buy_and_hold.id}).json()
        base = f'/runs/{run["run_id"]}'
        assert client.post(base+'/image-sets', headers=token, json={}).status_code == 409
        posts = client.post(base+'/social-posts', headers=token).json()
        response = client.post(base+'/image-sets', headers=token, json={})
        assert response.status_code == 200
        result = response.json()
        assert len(result['variants']) == 3 and len(provider.calls) == 1
        master_id = result['master']['id']
        assert client.get(base+'/image-sets',headers={'Authorization':'Bearer old-token'}).status_code == 401
        assert client.get(base+f'/image-sets/{master_id}/file', headers=token).status_code == 404
        for variant in result['variants']:
            path = base+f'/images/{variant["id"]}/file'
            assert client.get(path,headers={'Authorization':'Bearer old-token'}).status_code == 401
            assert client.get(path, headers=token).status_code == 200
            assert client.post(base+f'/images/{variant["id"]}/reviews', headers=token,
                               json={'action':'approved'}).status_code == 200
        for post in posts:
            client.post(base+f'/social-posts/{post["id"]}/reviews', headers=token,
                        json={'action':'approved'})
        client.post(base+'/reviews', headers=token, json={'action':'approved','expected_revision':run['revision']})
        bundle = client.get(base+'/bundle', headers=token)
        assert bundle.status_code == 200
        with ZipFile(BytesIO(bundle.content)) as archive:
            assert all('images/'+i['seo_filename'] in archive.namelist() for i in result['variants'])
        assert len(client.get(base+'/ai-calls', headers=token).json()) == 1


def test_task_routes_usage_and_no_quality_fallback(settings, tmp_path, monkeypatch):
    import litellm
    settings.demo_mode = False
    settings.anthropic_api_key = 'fake-anthropic'
    settings.gemini_api_key = 'fake-google'
    settings.seo_strategy_provider = 'anthropic'
    settings.seo_strategy_model = 'test-a'
    settings.topic_map_provider = 'google'
    settings.topic_map_model = 'test-b'
    settings.ai_model_capabilities = json.dumps({
        'anthropic/test-a': {'json_output': True, 'context_tokens': 100000},
        'google/test-b': {'json_output': True, 'context_tokens': 100000}})
    store = SQLiteStorage(tmp_path/'routes.sqlite')
    calls = []
    monkeypatch.setattr(litellm, 'token_counter', lambda **kwargs: 20)
    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=40, prompt_tokens=25,
            completion_tokens=15), choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content='{"passed": true}'))],
            _hidden_params={'response_cost': 0.002})
    monkeypatch.setattr(litellm, 'completion', completion)
    from uuid import uuid4
    run_id = str(uuid4())
    for task in ('seo_strategy', 'topic_map'):
        assert ModelRouter(settings, store).generate(run_id, CheckResult, 'Check', {}, model_role=task).passed
    assert [c['model'] for c in calls] == ['anthropic/test-a', 'gemini/test-b']
    assert {c.provider for c in store.list_ai_calls(run_id)} == {'anthropic', 'google'}
    assert all(c.cost == 0.002 and c.input_tokens == 25 for c in store.list_ai_calls(run_id))
    settings.ai_model_capabilities = '{}'
    with pytest.raises(ModelConfigurationError, match='seo_strategy'):
        ModelRouter(settings, store).generate(run_id, CheckResult, 'Check', {}, model_role='seo_strategy')


def test_fallback_only_for_transient_failures(settings, tmp_path, monkeypatch):
    import litellm
    from uuid import uuid4
    settings.demo_mode = False
    settings.openai_api_key = 'fake-openai'
    settings.anthropic_api_key = 'fake-anthropic'
    settings.seo_qa_provider = 'openai'
    settings.seo_qa_model = 'test-primary'
    settings.seo_qa_fallback_provider = 'anthropic'
    settings.seo_qa_fallback_model = 'test-backup'
    settings.ai_model_capabilities = json.dumps({
        'openai/test-primary': {'json_output': True, 'context_tokens': 100000},
        'anthropic/test-backup': {'json_output': True, 'context_tokens': 100000}})
    store = SQLiteStorage(tmp_path/'fallback.sqlite')
    monkeypatch.setattr(litellm, 'token_counter', lambda **kwargs: 20)
    calls = []
    def completion(**kwargs):
        calls.append(kwargs['model'])
        if len(calls) == 1:
            raise TimeoutError('temporary timeout')
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content='{"passed": true}'))], _hidden_params={})
    monkeypatch.setattr(litellm, 'completion', completion)
    monkeypatch.setattr(ModelRouter, '_transient', staticmethod(lambda llm, exc: isinstance(exc, TimeoutError)))
    run_id = str(uuid4())
    assert ModelRouter(settings, store).generate(run_id, CheckResult, 'Check', {}, model_role='seo_qa').passed
    assert calls == ['openai/test-primary', 'anthropic/test-backup']
    assert [c.fallback_used for c in store.list_ai_calls(run_id)] == [False, True]
    calls.clear()
    def invalid(**kwargs):
        calls.append(kwargs['model'])
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content='not JSON'))], _hidden_params={})
    monkeypatch.setattr(litellm, 'completion', invalid)
    with pytest.raises(Exception):
        ModelRouter(settings, store).generate(str(uuid4()), CheckResult, 'Check', {}, model_role='seo_qa')
    assert calls == ['openai/test-primary']


def test_image_provider_switch_and_configured_fallback(settings, monkeypatch):
    import httpx
    settings.image_generation_enabled = True
    settings.image_generation_provider = 'openai'
    settings.image_generation_model = 'gpt-image-2.5-flare'
    settings.openai_api_key = 'fake-openai'
    settings.image_generation_fallback_provider = 'google'
    settings.image_generation_fallback_model = 'gemini-3.1-flash-image'
    settings.gemini_api_key = 'fake-google'
    google = MasterProvider()
    calls = []
    def failed_openai(self, prompt, size):
        calls.append('openai')
        raise httpx.ConnectError('temporary connection failure')
    monkeypatch.setattr('src.services.images.OpenAIImageProvider.generate', failed_openai)
    monkeypatch.setattr('src.services.images.GoogleImageProvider.generate',
                        lambda self, prompt, size: google.generate(prompt, size))
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        SocialService(settings, workflow.storage).prepare(state)
        result = ImageSetService(settings, workflow.storage).create(state)
        assert calls == ['openai'] and len(google.calls) == 1
        assert result['master'].provider == 'google'
        assert result['master'].fallback_used is True
        ai_calls = workflow.storage.list_ai_calls(str(state.run_id))
        assert [(c.provider, c.status, c.fallback_used) for c in ai_calls] == [
            ('openai', 'failure', False), ('google', 'success', True)]


def test_compatible_image_route_and_optional_prompt(settings, monkeypatch):
    settings.image_generation_enabled = True
    settings.image_prompt_enabled = True
    settings.image_generation_provider = 'openai_compatible'
    settings.image_generation_model = 'vendor-image'
    settings.openai_compatible_api_key = 'fake-compatible'
    settings.openai_compatible_api_base = 'https://provider.example/v1'
    settings.ai_model_capabilities = json.dumps({
        'openai_compatible/vendor-image': {'image_generation': True}})
    provider = MasterProvider()
    roles = []
    from src.services.image_sets import ImagePrompt
    def fake_prompt(self, run_id, model_type, task, payload, drafting=False, model_role=None):
        roles.append(model_role)
        return ImagePrompt(prompt='A clean architectural illustration with a central blue office, neutral background and wide safe margins.')
    monkeypatch.setattr(ModelRouter, 'generate', fake_prompt)
    monkeypatch.setattr('src.services.images.CompatibleImageProvider.generate',
                        lambda self, prompt, size: provider.generate(prompt, size))
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        SocialService(settings, workflow.storage).prepare(state)
        result = ImageSetService(settings, workflow.storage).create(state)
        assert roles == ['image_prompt']
        assert len(provider.calls) == 1
        assert result['master'].provider == 'openai_compatible'
        assert all(v.optimization_status == 'completed' for v in result['variants'])


def test_missing_image_capability_rejected_before_provider_call(settings, monkeypatch):
    settings.image_generation_enabled = True
    settings.image_generation_provider = 'openai_compatible'
    settings.image_generation_model = 'vendor-image'
    settings.openai_compatible_api_key = 'fake-compatible'
    settings.openai_compatible_api_base = 'https://provider.example/v1'
    settings.ai_model_capabilities = json.dumps({
        'openai_compatible/vendor-image': {'image_generation': False}})
    calls = []
    monkeypatch.setattr('src.services.images.CompatibleImageProvider.generate',
                        lambda self, prompt, size: calls.append(size))
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        SocialService(settings, workflow.storage).prepare(state)
        with pytest.raises(ModelConfigurationError, match='image_generation'):
            ImageSetService(settings, workflow.storage).create(state)
        assert calls == []


def test_master_too_small_fails_without_upscaling(settings):
    settings.image_generation_enabled = True
    class SmallProvider(MasterProvider):
        def generate(self, prompt, size):
            self.calls.append((prompt, size))
            output = BytesIO()
            Image.new('RGB', (1024, 1024), '#E6EEF6').save(output, format='PNG')
            return output.getvalue(), None
    provider = SmallProvider()
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        SocialService(settings, workflow.storage).prepare(state)
        with pytest.raises(ImageMasterError, match='invalid master'):
            ImageSetService(settings, workflow.storage, provider).create(state)
        assert len(provider.calls) == 1
        assert workflow.storage.list_assets(str(state.run_id), 'master') == []
        assert workflow.get(str(state.run_id)).status == 'seo_check_passed'


def test_google_and_compatible_adapter_request_shapes(settings, monkeypatch):
    import httpx
    payload, _ = MasterProvider().generate('test', (2048, 2048))
    seen = []
    real_client = httpx.Client
    def handler(request):
        data = json.loads(request.content)
        seen.append((str(request.url), data))
        if 'generativelanguage' in str(request.url):
            assert request.headers['x-goog-api-key'] == 'fake-google'
            assert data['generationConfig']['imageConfig'] == {'aspectRatio':'1:1','imageSize':'2K'}
            return httpx.Response(200, json={'candidates':[{'content':{'parts':[
                {'thought':True,'inlineData':{'mimeType':'image/png','data':'ignored'}},
                {'inlineData':{'mimeType':'image/png','data':base64.b64encode(payload).decode()}}]}}]})
        assert request.headers['authorization'] == 'Bearer fake-compatible'
        assert data['model'] == 'vendor-image' and data['n'] == 1
        return httpx.Response(200, json={'data':[{'b64_json':base64.b64encode(payload).decode()}]})
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(transport=transport, **kwargs))
    assert GoogleImageProvider(settings, 'gemini-3.1-flash-image', 'fake-google').generate('prompt',(2048,2048))[0] == payload
    assert CompatibleImageProvider(settings, 'vendor-image', 'fake-compatible',
                                   'https://provider.example/v1').generate('prompt',(2048,2048))[0] == payload
    assert seen[0][0].endswith('/models/gemini-3.1-flash-image:generateContent')
    assert seen[1][0] == 'https://provider.example/v1/images/generations'


def test_future_task_route_uses_configuration(settings, tmp_path, monkeypatch):
    import litellm
    from uuid import uuid4
    settings.demo_mode = False
    settings.mistral_api_key = 'fake-mistral'
    settings.ai_task_routes = json.dumps({'future_task': {'provider':'mistral', 'model':'future-model'}})
    settings.ai_model_capabilities = json.dumps({'mistral/future-model': {
        'json_output': True, 'context_tokens': 100000}})
    monkeypatch.setattr(litellm, 'token_counter', lambda **kwargs: 10)
    names = []
    def completion(**kwargs):
        names.append(kwargs['model'])
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content='{"passed": true}'))], _hidden_params={})
    monkeypatch.setattr(litellm, 'completion', completion)
    store = SQLiteStorage(tmp_path/'future.sqlite')
    assert ModelRouter(settings, store).generate(str(uuid4()), CheckResult, 'Check', {},
                                                 model_role='future_task').passed
    assert names == ['mistral/future-model']


def test_group_variant_retry_is_authenticated_and_stale_versions_cannot_be_approved(settings, monkeypatch):
    settings.image_generation_enabled = True
    settings.image_api_key = 'fake-test-key'
    provider = MasterProvider()
    monkeypatch.setattr('src.services.images.OpenAIImageProvider.generate',
                        lambda self, prompt, size: provider.generate(prompt, size))
    from src.services.images import process_image as original
    def fail_linkedin(raw, platform, png_requested, image_settings, allow_upscale=True):
        if platform == 'linkedin':
            raise ValueError('simulated failure')
        return original(raw, platform, png_requested, image_settings, allow_upscale)
    monkeypatch.setattr('src.services.images.process_image', fail_linkedin)
    with TestClient(create_app(settings)) as client:
        headers = authenticated_headers(client)
        run = client.post('/runs', headers=headers, json={'icp_id':icp_1_buy_and_hold.id}).json()
        base = f'/runs/{run["run_id"]}'
        client.post(base+'/social-posts', headers=headers)
        result = client.post(base+'/image-sets', headers=headers, json={}).json()
        master_id = result['master']['id']
        variant = next(i for i in result['variants'] if i['platform']=='linkedin')
        path = base+f'/image-sets/{master_id}/process/linkedin'
        assert client.post(path,headers={'Authorization':'Bearer old-token'}).status_code == 401
        assert client.post(base+f'/images/{variant["id"]}/reviews', headers=headers,
                           json={'action':'approved'}).status_code == 409
        monkeypatch.setattr('src.services.images.process_image', original)
        assert client.post(path, headers=headers).status_code == 200
        assert len(provider.calls) == 1
        revised = client.post(base+'/reviews', headers=headers, json={
            'action':'needs_revision', 'feedback_target_agent':'content_agent',
            'feedback_notes':'Bitte ergänzen Sie eine klare Checkliste für das Gespräch.',
            'expected_revision':run['revision']})
        assert revised.status_code == 200
        assert client.post(base+f'/images/{variant["id"]}/reviews', headers=headers,
                           json={'action':'approved'}).status_code == 409
