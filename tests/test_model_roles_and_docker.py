import pytest
import yaml
from src.services.llm import ModelRouter

@pytest.mark.parametrize('role,expected',[('planning','proxy/planning'),('briefing','proxy/briefing'),('drafting','proxy/drafting'),('brand','proxy/brand')])
def test_separate_role_models(settings,role,expected):
    settings.llm_model_planning='proxy/planning'
    settings.llm_model_briefing='proxy/briefing'
    settings.llm_model_drafting='proxy/drafting'
    settings.llm_model_brand='proxy/brand'
    assert ModelRouter(settings,None).select_model(role)==expected

def test_role_fallbacks(settings):
    settings.llm_model_briefing=''
    settings.llm_model_brand=''
    router=ModelRouter(settings,None)
    assert router.select_model('briefing')==settings.llm_model_drafting
    assert router.select_model('brand')==settings.llm_model_planning

def test_compose_loopback_secrets_and_health_order(settings):
    root=settings.input_dir.parent
    config=yaml.safe_load((root/'compose.yaml').read_text())
    backend=config['services']['backend'];frontend=config['services']['frontend']
    assert backend['ports']==['127.0.0.1:8000:8000']
    assert frontend['ports']==['127.0.0.1:3000:3000']
    assert backend['env_file']=='.env'
    assert 'env_file' not in frontend
    assert frontend['depends_on']['backend']['condition']=='service_healthy'
    assert backend['read_only'] and frontend['read_only']


def test_non_property_icp_needs_no_schema_change():
    from src.schemas.icp import ICP, icp_1_buy_and_hold
    company=ICP(id='icp_3_gmbh_ug',name='GmbHs und UGs',description='Kapitalgesellschaften',goals=['Jahresabschluss vorbereiten'],services=['Jahresabschluss'],preferred_traits=['Digitale Belege'],seed_topic='Jahresabschluss GmbH')
    assert company.minimum_rented_properties is None
    assert icp_1_buy_and_hold.minimum_rented_properties==2
