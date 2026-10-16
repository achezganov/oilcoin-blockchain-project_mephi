import os
import tempfile
import sys
from pathlib import Path
import pytest

_project_root = Path(os.environ.get('OILCOIN_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(_project_root))

_test_directory = tempfile.TemporaryDirectory(prefix="oilcoin-tests-")
os.environ["DATABASE_PATH"] = os.path.join(_test_directory.name, "bootstrap.db")
os.environ["SECRET_KEY"] = "test-only-secret-key-with-more-than-32-characters"
os.environ["MINING_DIFFICULTY"] = "1"
os.environ["APP_ENV"] = "development"

@pytest.fixture
def application(tmp_path):
    from app import create_app
    return create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "test.db"),
        "SECRET_KEY": "test-only-secret-key-with-more-than-32-characters", "MINING_DIFFICULTY": 1,
        "ENABLE_DEMO": False, "ALLOW_MANUAL_MINING": True, "ALLOW_LEGACY_PRODUCTION": True})

@pytest.fixture
def client(application):
    return application.test_client()

def post(client, path, data=None):
    token = client.get("/api/csrf").json["csrf_token"]
    return client.post(path, data={**(data or {}), "csrf_token": token})

def register(client, label="Student"):
    response = post(client, "/register", {"label": label,"password": "student-pass-123", "password_confirm": "student-pass-123"})
    assert response.status_code == 302
    with client.session_transaction() as session:
        return session["wallet_address"]
