import os
import socket
import ipaddress
import tempfile
from pathlib import Path

# This must run before importing application modules or LiteLLM.
# Tests use fake transports and must not depend on a host proxy configuration.
for _proxy in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
    os.environ.pop(_proxy, None)
os.environ['LITELLM_MODE'] = 'PRODUCTION'
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'

import pytest
from cryptography.fernet import Fernet
from src.config.settings import Settings

# Even Settings() in an old test must not open the project's live .env file.
Settings.model_config['env_file'] = None

for _field in Settings.model_fields:
    os.environ.pop(_field.upper(), None)
_test_root = Path(tempfile.mkdtemp(prefix='muster-offline-tests-'))
os.environ.update(COMBINED_MODE='false', DEMO_MODE='true', IMAGE_PROMPT_ENABLED='false',
                  IMAGE_GENERATION_ENABLED='false', STORAGE_BACKEND='sqlite',
                  DATA_DIR=str(_test_root / 'var'), OUTPUT_DIR=str(_test_root / 'outputs'),
                  AUTH_ENCRYPTION_KEY=Fernet.generate_key().decode())
for _field in Settings.model_fields:
    if _field.endswith(('api_key', 'access_token')) or _field in {
        'dataforseo_login', 'dataforseo_password', 'database_url'}:
        os.environ[_field.upper()] = ''

_socket_connect = socket.socket.connect
_socket_connect_ex = socket.socket.connect_ex


def _allow_test_connection(address):
    if not isinstance(address, tuple):
        return True  # Local socket pairs used by the ASGI test client.
    try:
        return ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        return address[0] == 'localhost'


def _offline_connect(self, address):
    if not _allow_test_connection(address):
        raise AssertionError('External networking is disabled in automated tests')
    return _socket_connect(self, address)


def _offline_connect_ex(self, address):
    if not _allow_test_connection(address):
        raise AssertionError('External networking is disabled in automated tests')
    return _socket_connect_ex(self, address)


socket.socket.connect = _offline_connect
socket.socket.connect_ex = _offline_connect_ex

@pytest.fixture
def settings(tmp_path):
    # Existing cases exercise the legacy checkpoint path; decision tests opt in explicitly.
    return Settings(_env_file=None,demo_mode=True,seo_decisions_enabled=False,data_dir=tmp_path/'var',output_dir=tmp_path/'outputs',auth_encryption_key=Fernet.generate_key().decode())


def authenticated_headers(client, name='Alex'):
    auth = client.app.state.auth
    user = auth.store.user('email', name.lower()+'@example.org')
    if user is None:
        user = auth.store.create_user(name.lower()+'@example.org', name, 'reviewer', 'temporary-password-123')
    session, csrf = auth.session(user['id'])
    client.cookies.set('review_session', session)
    client.cookies.set('review_csrf', csrf)
    return {'X-CSRF-Token': csrf}


def own_fixture(client, state):
    """Explicitly own a legacy fixture; production never infers owners from names."""
    user = client.app.state.auth.identify(client.cookies.get('review_session'))
    assert user
    store = client.app.state.auth.store
    store.execute(f'INSERT INTO {store.prefix}run_owners VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET user_id=excluded.user_id',
                  (str(state.run_id), user['id']))
    state.owner_user_id = user['id']
    return state
