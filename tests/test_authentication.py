"""Account, MFA, session, and administrator security boundaries."""

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.app import create_app
from src.config.settings import Settings
from src.security.auth import AuthService, digest, totp_code
from src.storage.sqlite_storage import SQLiteStorage
from scripts.bootstrap import bootstrap


def enroll(client, email='sam@example.org', temporary='temporary-password-123', password='new-strong-password-456'):
    login = client.post('/auth/login',json={'email':email,'password':temporary})
    assert login.status_code == 200
    challenge = login.json()['challenge']
    assert login.json()['stage'] == 'setup_password'
    assert client.post('/auth/setup/password',json={'challenge':challenge,'password':password}).status_code == 200
    setup = client.post('/auth/setup/mfa',json={'challenge':challenge}).json()
    response = client.post('/auth/setup/confirm',json={'challenge':challenge,'code':totp_code(setup['secret'])})
    assert response.status_code == 200
    return setup['secret'], response.json()


def test_first_admin_mfa_session_csrf_recovery_and_logout(settings):
    with TestClient(create_app(settings)) as client:
        auth=client.app.state.auth
        auth.bootstrap_admin('Sam@example.org','temporary-password-123')
        with pytest.raises(ValueError): auth.bootstrap_admin('other@example.org','temporary-password-123')
        assert client.get('/runs').status_code == 401
        secret, signed = enroll(client)
        # Replay the enrolled code even if this test crosses a 30-second TOTP boundary.
        already_used_totp = totp_code(secret, auth.store.user('email','sam@example.org')['last_totp_step'])
        assert signed['reviewer']=='Sam' and signed['role']=='admin'
        assert secret not in auth.store.user('email','sam@example.org')['totp_secret']
        assert len(signed['recovery_codes'])==10
        assert client.get('/session').json()['reviewer']=='Sam'
        reopened=AuthService(client.app.state.workflow.storage,settings.auth_encryption_key.get_secret_value())
        assert reopened.identify(client.cookies.get('review_session'))['id']==signed['user_id']
        assert client.post('/jobs',json={}).status_code==403
        assert client.get('/runs',headers={'Authorization':'Bearer legacy-token'}).status_code==401
        headers={'X-CSRF-Token':signed['csrf_token']}
        assert client.get('/runs',headers=headers).status_code==200
        assert client.post('/session/logout',headers=headers).status_code==200
        assert client.get('/session').status_code==401
        login=client.post('/auth/login',json={'email':'sam@example.org','password':'new-strong-password-456'}).json()
        assert login['stage']=='mfa'
        recovery=signed['recovery_codes'][0]
        assert client.post('/auth/verify',json={'challenge':login['challenge'],'code':recovery}).status_code==200
        signed2=client.get('/session').json()
        assert signed2['user_id']==signed['user_id']
        assert client.post('/session/logout',headers={'X-CSRF-Token':signed2['csrf_token']}).status_code==200
        login2=client.post('/auth/login',json={'email':'sam@example.org','password':'new-strong-password-456'}).json()
        assert client.post('/auth/verify',json={'challenge':login2['challenge'],'code':recovery}).status_code==401
        assert client.post('/auth/verify',json={'challenge':login2['challenge'],'code':already_used_totp}).status_code==401


def test_admin_creation_reset_disable_and_audit_identity(settings):
    with TestClient(create_app(settings)) as client:
        auth=client.app.state.auth
        auth.bootstrap_admin('sam@example.org','temporary-password-123')
        secret,signed=enroll(client)
        csrf={'X-CSRF-Token':signed['csrf_token']}
        body={'email':'alex@example.org','name':'Alex','admin_password':'new-strong-password-456','admin_code':totp_code(secret)}
        created=client.post('/admin/users',headers=csrf,json=body)
        assert created.status_code==200
        user=created.json()['user']
        assert user['role']=='reviewer' and created.json()['temporary_password']
        assert len(client.get('/admin/users').json())==2
        target_secret,_=auth.session(user['id'])
        assert auth.identify(target_secret)
        reset=client.post(f"/admin/users/{user['id']}/reset",headers=csrf,
                          json={'admin_password':body['admin_password'],'admin_code':body['admin_code']})
        assert reset.status_code==200 and reset.json()['temporary_password']
        assert auth.identify(target_secret) is None
        assert client.post('/auth/login',json={'email':user['email'],'password':created.json()['temporary_password']}).status_code==401
        assert client.post('/auth/login',json={'email':user['email'],'password':reset.json()['temporary_password']}).json()['stage']=='setup_password'
        assert client.post(f"/admin/users/{user['id']}/disable",headers=csrf,
                           json={'admin_password':body['admin_password'],'admin_code':body['admin_code']}).status_code==200
        assert client.post('/auth/login',json={'email':user['email'],'password':reset.json()['temporary_password']}).status_code==401
        assert client.post(f"/admin/users/{user['id']}/enable",headers=csrf,
                           json={'admin_password':body['admin_password'],'admin_code':body['admin_code']}).status_code==200


def test_review_records_stable_user_id(settings):
    with TestClient(create_app(settings)) as client:
        auth=client.app.state.auth
        user=auth.store.create_user('alex@example.org','Alex','reviewer','temporary-password-123')
        session,csrf=auth.session(user['id'])
        client.cookies.set('review_session',session)
        client.cookies.set('review_csrf',csrf)
        headers={'X-CSRF-Token':csrf}
        run=client.post('/runs',headers=headers,json={'icp_id':'icp_1_buy_and_hold'}).json()
        result=client.post(f"/runs/{run['run_id']}/reviews",headers=headers,
                           json={'action':'approved','expected_revision':run['revision']})
        assert result.status_code==200
        assert result.json()['review_history'][-1]['reviewer_id']==user['id']
        assert result.json()['review_history'][-1]['reviewer_name']=='Alex'


def test_login_throttle_and_expired_session(settings):
    with TestClient(create_app(settings)) as client:
        auth=client.app.state.auth
        user=auth.bootstrap_admin('sam@example.org','temporary-password-123')
        for _ in range(5):
            assert client.post('/auth/login',json={'email':user['email'],'password':'wrong'}).status_code==401
        assert client.post('/auth/login',json={'email':user['email'],'password':'temporary-password-123'}).status_code==401
        session,csrf=auth.session(user['id'])
        client.cookies.set('review_session',session)
        client.cookies.set('review_csrf',csrf)
        assert client.get('/session').status_code==200
        with auth.store.storage.connect() as db:
            db.execute('UPDATE auth_sessions SET expires_at=? WHERE digest=?',
                       ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),digest(session)))
        assert client.get('/session').status_code==401


def test_legacy_sessions_are_invalidated_and_reviewer_cannot_manage_accounts(settings):
    storage=SQLiteStorage(settings.data_dir/'runs.sqlite')
    storage.create_session('legacy-digest','Alex',(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    auth=AuthService(storage,settings.auth_encryption_key.get_secret_value())
    assert storage.get_session('legacy-digest') is None
    with TestClient(create_app(settings)) as client:
        user=client.app.state.auth.store.create_user('alex@example.org','Alex','reviewer','temporary-password-123')
        session,csrf=client.app.state.auth.session(user['id'])
        client.cookies.set('review_session',session)
        client.cookies.set('review_csrf',csrf)
        assert client.get('/admin/users').status_code==403
        assert client.post('/admin/users',headers={'X-CSRF-Token':csrf},json={
            'email':'other@example.org','name':'Other','admin_password':'temporary-password-123','admin_code':'000000'
        }).status_code==403


def test_hosted_origin_requires_https_and_secure_cookie(settings):
    with pytest.raises(ValidationError):
        Settings(_env_file=None,auth_public_origin='http://portal.example.org')
    settings.auth_public_origin='https://portal.example.org'
    with TestClient(create_app(settings)) as client:
        client.app.state.auth.bootstrap_admin('sam@example.org','temporary-password-123')
        login=client.post('/auth/login',json={'email':'sam@example.org','password':'temporary-password-123'}).json()
        challenge=login['challenge']
        client.post('/auth/setup/password',json={'challenge':challenge,'password':'new-strong-password-456'})
        setup=client.post('/auth/setup/mfa',json={'challenge':challenge}).json()
        signed=client.post('/auth/setup/confirm',json={'challenge':challenge,'code':totp_code(setup['secret'])})
        assert signed.status_code==200
        cookie=signed.headers['set-cookie'].lower()
        assert 'secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie


def test_bootstrap_migrates_old_tokens_without_rotating_existing_key(tmp_path):
    path=tmp_path/'.env'
    template=tmp_path/'.env.example'
    template.write_text('DEMO_MODE=true\nAUTH_ENCRYPTION_KEY=\n',encoding='utf-8')
    path.write_text('DEMO_MODE=false\nREVIEWER_ALEX_TOKEN=old\nREVIEWER_SAM_TOKEN=old2\n',encoding='utf-8')
    bootstrap(path,template)
    updated=path.read_text(encoding='utf-8')
    assert 'DEMO_MODE=false' in updated
    assert 'REVIEWER_' not in updated
    key=next(line for line in updated.splitlines() if line.startswith('AUTH_ENCRYPTION_KEY='))
    assert len(key.split('=',1)[1])==44
    bootstrap(path,template)
    assert path.read_text(encoding='utf-8')==updated


@pytest.mark.skipif(not os.environ.get('TEST_POSTGRES_DSN'), reason='Dedicated Postgres test database not configured')
def test_postgres_account_storage_and_session():
    from cryptography.fernet import Fernet
    from src.storage.postgres_storage import PostgresStorage
    store=PostgresStorage(os.environ['TEST_POSTGRES_DSN'])
    auth=AuthService(store,Fernet.generate_key().decode())
    email=f'reviewer-{uuid4()}@example.org'
    user=auth.store.create_user(email,'Test reviewer','reviewer','temporary-password-123')
    session,csrf=auth.session(user['id'])
    assert auth.identify(session,csrf)['email']==email
    auth.store.revoke_user(user['id'])
    assert auth.identify(session) is None
