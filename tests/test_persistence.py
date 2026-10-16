import json
from pathlib import Path
import pytest
from accounts import find_wallet_account, decrypt_private_key
from conftest import post, register
from database import connect, unit_of_work
from scripts.backup import create_backup, verify_snapshot
from storage import load_blockchain


def test_backup_restores_pending_payment_keys_and_progress(application, client, tmp_path):
    address = register(client)
    post(client, '/mine/quick')
    post(client, '/game/upgrade')
    source = Path(application.config['DATABASE_PATH'])
    backup = tmp_path / 'snapshot.db'
    create_backup(source, backup)
    verify_snapshot(backup)
    assert backup.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        create_backup(source, backup)
    from app import create_app
    restored_app = create_app({**dict(application.config), 'DATABASE_PATH': str(backup)})
    restored = restored_app.test_client()
    response = post(restored, '/login', {'address': address, 'password': 'student-pass-123'})
    assert response.status_code == 302
    assert restored.get('/api/game').json['upgrade_pending']
    assert post(restored, '/mine/quick').status_code == 302
    assert restored.get('/api/game').json['rig_level'] == 1
    assert len(restored.get('/api/pending').json) == 0
    # The original database is not affected by restoring and modifying its snapshot.
    assert len(client.get('/api/pending').json) == 1
    with application.app_context(), unit_of_work(source, write=False):
        assert len(decrypt_private_key(find_wallet_account(address))) == 64


def test_failed_payment_write_rolls_back_upgrade_and_pending(application, client, monkeypatch):
    import app as module
    address = register(client)
    post(client, '/mine/quick')
    before = client.get('/api/game').json
    def fail(*args):
        raise ValueError('Injected persistence failure')
    monkeypatch.setattr(module, 'save_blockchain', fail)
    assert post(client, '/game/upgrade').status_code == 503
    assert client.get('/api/game').json == before
    assert client.get('/api/pending').json == []
    assert client.get(f'/api/balance/{address}').json['available_balance'] == 50


def test_wrong_encryption_secret_stops_startup(application, client):
    register(client)
    from app import create_app
    with pytest.raises(RuntimeError, match='cannot decrypt'):
        create_app({**dict(application.config), 'SECRET_KEY':'different-secret-that-is-long-enough'})


def test_storage_and_backup_detect_relational_index_corruption(application, client, tmp_path):
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('UPDATE blocks SET hash=? WHERE height=0', ('f'*64,))
    assert client.get('/health').status_code == 503
    with pytest.raises(ValueError):
        create_backup(Path(application.config['DATABASE_PATH']), tmp_path/'invalid.db')
    assert not (tmp_path/'invalid.db').exists()


def test_rate_limiting_and_untrusted_forwarded_header(application, client, monkeypatch):
    import app as module
    monkeypatch.setattr(module.time, 'time', lambda: 1_900_000_010)
    register(client)
    token = client.get('/api/csrf').json['csrf_token']
    for index in range(20):
        response = client.post('/login', data={'address':'invalid','password':'invalid','csrf_token':token}, headers={'X-Forwarded-For':f'198.51.100.{index}'})
        assert response.status_code == 401
    assert post(client,'/login',{'address':'invalid','password':'invalid'}).status_code == 429


def test_invalid_address_apis_and_xss(client):
    for path in ('/api/balance/invalid', '/api/wallet/invalid/stats', '/api/wallet/invalid/operations', '/api/address/invalid'):
        assert client.get(path).status_code == 400
    register(client, '<script>alert(1)</script>')
    page = client.get('/dashboard').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in page
    assert '&lt;script&gt;' in page
