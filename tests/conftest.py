import os
import shutil
import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy.engine import make_url


TEST_ROOT = tempfile.mkdtemp(prefix='bustrack-tests-')
INSTANCE_DIR = os.path.join(TEST_ROOT, 'instance')
UPLOAD_DIR = os.path.join(TEST_ROOT, 'uploads')
DB_PATH = os.path.join(TEST_ROOT, 'test.db')


def _database_url_for_tests():
    override = os.environ.get('TEST_DATABASE_URL')
    if not override:
        return f'sqlite:///{DB_PATH}'
    parsed = make_url(override)
    backend = parsed.get_backend_name()
    if parsed.query:
        raise RuntimeError(
            'TEST_DATABASE_URL query parameters are not allowed for destructive tests.')
    if backend == 'sqlite':
        selected = Path(parsed.database or '').resolve()
        if Path(TEST_ROOT).resolve() not in selected.parents:
            raise RuntimeError(
                'TEST_DATABASE_URL SQLite files must be inside the generated TEST_ROOT.')
        return override
    if backend in {'postgresql', 'postgres'}:
        database_name = (parsed.database or '').lower()
        if parsed.host not in {'127.0.0.1', 'localhost', '::1'}:
            raise RuntimeError('PostgreSQL tests require a loopback database host.')
        if not database_name.startswith('d205_test_'):
            raise RuntimeError(
                'PostgreSQL test database names must start with d205_test_.')
        if os.environ.get('D205_ALLOW_DESTRUCTIVE_TEST_DATABASE') != '1':
            raise RuntimeError(
                'Set D205_ALLOW_DESTRUCTIVE_TEST_DATABASE=1 for the disposable test database.')
        return override
    raise RuntimeError(f'Unsupported TEST_DATABASE_URL backend: {backend}')

os.environ.update({
    'DATABASE_URL': _database_url_for_tests(),
    'INSTANCE_DIR': INSTANCE_DIR,
    'UPLOAD_FOLDER': UPLOAD_DIR,
    'DISABLE_SCHEDULER': '1',
    'SECRET_KEY': 'test-session-secret-that-is-not-used-outside-tests',
    'FLASK_ENV': 'testing',
    'INSTALL_TOKEN': 'test-install-token-that-is-long-and-randomized',
    'SMTP_ALLOWED_HOSTS': 'smtp.example.test,alternate.example.test',
    'BACKUP_ENCRYPTION_KEY': Fernet.generate_key().decode('ascii'),
    'EMAIL_CREDENTIAL_ENCRYPTION_KEY': Fernet.generate_key().decode('ascii'),
    'LOGIN_RATE_LIMIT_ATTEMPTS': '3',
    'LOGIN_RATE_LIMIT_WINDOW_SECONDS': '300',
    'LOGIN_RATE_LIMIT_LOCK_SECONDS': '300',
})

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import app as application


@pytest.fixture(autouse=True)
def clean_database():
    application.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    with application.app.app_context():
        application.db.drop_all()
        application.db.create_all()
        application._seed_defaults()
        application._seed_phase2_security_and_imports()
        admin_group = application.UserGroup.query.filter_by(is_admin=True).one()
        admin = application.User(
            username='admin', email='admin@example.test', first_name='Admin',
            group_id=admin_group.id, active=True,
        )
        admin.set_password('Correct-Horse-Battery-Staple')
        application.db.session.add(admin)
        application.db.session.commit()
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    application._mark_installed()
    yield
    with application.app.app_context():
        application.db.session.remove()


@pytest.fixture
def client():
    return application.app.test_client()


def csrf_token(client):
    client.get('/admin/login')
    with client.session_transaction() as sess:
        token = sess.get('_csrf')
        if not token:
            token = 'test-csrf-token'
            sess['_csrf'] = token
    return token


def login(client, username='admin', password='Correct-Horse-Battery-Staple'):
    token = csrf_token(client)
    return client.post('/admin/login', data={
        '_csrf': token, 'username': username, 'password': password,
    }, follow_redirects=False)


@pytest.fixture
def logged_in_client(client):
    response = login(client)
    assert response.status_code == 302
    return client


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
