import io
import json
import os
import base64
from datetime import date
from pathlib import Path

from cryptography.fernet import Fernet

import app as application
from conftest import INSTANCE_DIR, csrf_token, login


def add_group(name, permissions):
    group = application.UserGroup(name=name, is_admin=False)
    application.db.session.add(group)
    application.db.session.flush()
    for module, level in permissions.items():
        application.db.session.add(application.GroupPermission(
            group_id=group.id, module_key=module, access_level=level))
    application.db.session.commit()
    return group


def add_user(username, group, password='Another-Safe-Password'):
    user = application.User(username=username, group_id=group.id, active=True)
    user.set_password(password)
    application.db.session.add(user)
    application.db.session.commit()
    return user


def test_install_endpoints_are_gone_after_install(client):
    assert client.get('/install').status_code == 404
    assert client.post('/install/test-db', json={}).status_code == 404
    assert client.post('/install/run', json={}).status_code == 404


def test_install_requires_token_and_ignores_caller_database_destination(client, tmp_path):
    with application.app.app_context():
        application.User.query.delete()
        application.db.session.commit()
    os.remove(application.INSTALLED_FILE)
    assert client.post('/install/test-db', json={
        'type': 'sqlite', 'path': 'safe.db', 'install_token': 'wrong',
    }).status_code == 403
    target = tmp_path / 'outside.db'
    response = client.post('/install/test-db', json={
        'type': 'sqlite', 'path': str(target),
        'install_token': os.environ['INSTALL_TOKEN'],
    })
    assert response.status_code == 200
    assert response.get_json()['ok'] is True
    assert not target.exists()


def test_setup_stays_closed_if_filesystem_marker_is_lost(client):
    os.remove(application.INSTALLED_FILE)
    assert client.get('/install').status_code == 404
    assert client.post('/install/test-db', json={
        'install_token': os.environ['INSTALL_TOKEN'],
    }).status_code == 404


def test_install_creates_one_admin_and_permanently_closes_setup(client):
    with application.app.app_context():
        application.User.query.delete()
        application.db.session.commit()
    os.remove(application.INSTALLED_FILE)

    response = client.post('/install/run', json={
        'install_token': os.environ['INSTALL_TOKEN'],
        'username': 'initial.admin',
        'email': 'initial.admin@example.test',
        'password': 'A-Strong-Initial-Password',
    })

    assert response.status_code == 200
    assert response.get_json()['ok'] is True
    assert os.path.exists(application.INSTALLED_FILE)
    with application.app.app_context():
        admins = application.User.query.all()
        assert len(admins) == 1
        assert admins[0].username == 'initial.admin'
        assert admins[0].is_admin
    assert client.get('/install').status_code == 404
    assert client.post('/install/run', json={}).status_code == 404


def test_instance_secret_persistence_preserves_existing_configuration():
    env_path = Path(INSTANCE_DIR) / '.env'
    env_path.write_text(
        '# deployment settings\n'
        'DATABASE_URL="postgresql://database.example/app"\n'
        'SECRET_KEY="old-value"\n'
        'BACKUP_ENCRYPTION_KEY="preserve-this-value"\n'
        'export SECRET_KEY="duplicate-old-value"\n',
        encoding='utf-8',
    )

    application._write_instance_env('new-session-secret')

    content = env_path.read_text(encoding='utf-8')
    assert '# deployment settings' in content
    assert 'DATABASE_URL="postgresql://database.example/app"' in content
    assert 'BACKUP_ENCRYPTION_KEY="preserve-this-value"' in content
    assert content.count('SECRET_KEY=') == 1
    assert 'SECRET_KEY="new-session-secret"' in content
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_config_limited_cannot_post_any_section(client):
    with application.app.app_context():
        group = add_group('Config Readers', {'config': 'limited'})
        add_user('reader', group)
    assert login(client, 'reader', 'Another-Safe-Password').status_code == 302
    token = csrf_token(client)
    response = client.post('/admin/config', data={
        '_csrf': token, 'section': 'general', 'app_name': 'Changed',
    })
    assert response.status_code == 403
    with application.app.app_context():
        assert application.get_config().app_name != 'Changed'


def test_users_full_cannot_assign_an_administrator_group(client):
    with application.app.app_context():
        group = add_group('User Managers', {'users': 'full'})
        add_user('manager', group)
        admin_group_id = application.UserGroup.query.filter_by(is_admin=True).one().id
    login(client, 'manager', 'Another-Safe-Password')
    response = client.post('/admin/users/add', data={
        '_csrf': csrf_token(client), 'username': 'escalated',
        'password': 'Escalated-Safe-Password', 'group_id': admin_group_id,
    })
    assert response.status_code == 403
    with application.app.app_context():
        assert application.User.query.filter_by(username='escalated').first() is None


def test_users_full_cannot_assign_a_group_with_greater_capabilities(client):
    with application.app.app_context():
        manager_group = add_group('User Managers', {'users': 'full'})
        privileged_group = add_group('Configuration Managers', {'config': 'full'})
        add_user('manager', manager_group)
        privileged_group_id = privileged_group.id
    login(client, 'manager', 'Another-Safe-Password')
    response = client.post('/admin/users/add', data={
        '_csrf': csrf_token(client), 'username': 'overprivileged',
        'password': 'Escalated-Safe-Password', 'group_id': privileged_group_id,
    })
    assert response.status_code == 403


def test_disabled_account_session_is_revoked_on_next_request(logged_in_client):
    with application.app.app_context():
        admin = application.User.query.filter_by(username='admin').one()
        admin.active = False
        application.db.session.commit()
    response = logged_in_client.get('/admin/dashboard')
    assert response.status_code == 302
    assert '/admin/login' in response.headers['Location']


def test_database_backed_login_throttle_returns_429(client):
    token = csrf_token(client)
    for _ in range(application.app.config['LOGIN_RATE_LIMIT_ATTEMPTS']):
        client.post('/admin/login', data={
            '_csrf': token, 'username': 'admin', 'password': 'wrong-password',
        })
    response = client.post('/admin/login', data={
        '_csrf': token, 'username': 'admin', 'password': 'wrong-password',
    })
    assert response.status_code == 429
    with application.app.app_context():
        assert application.LoginThrottle.query.count() == 1


def test_open_redirect_is_rejected(client):
    response = client.post('/admin/login?next=//evil.example/path', data={
        '_csrf': csrf_token(client), 'username': 'admin',
        'password': 'Correct-Horse-Battery-Staple',
    })
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/admin/dashboard')


def test_nonadmin_cannot_download_or_restore_full_backup(client):
    with application.app.app_context():
        group = add_group('Config Managers', {'config': 'full'})
        add_user('configmanager', group)
    login(client, 'configmanager', 'Another-Safe-Password')
    assert client.get('/admin/config/export-json').status_code == 403
    response = client.post('/admin/config/import-db', data={
        '_csrf': csrf_token(client),
        'backup_file': (io.BytesIO(b'not-a-backup'), 'backup.bustrack'),
    }, content_type='multipart/form-data')
    assert response.status_code == 403


def test_operational_export_is_redacted(client):
    with application.app.app_context():
        group = add_group('Config Managers', {'config': 'full'})
        add_user('configmanager', group)
        cfg = application.get_config()
        cfg.mail_password = 'must-not-leak'
        cfg.twilio_auth_token = 'must-not-leak-either'
        application.db.session.commit()
    login(client, 'configmanager', 'Another-Safe-Password')
    response = client.get('/admin/config/export-operational-json')
    assert response.status_code == 200
    payload = response.get_data(as_text=True)
    assert 'must-not-leak' not in payload
    assert 'password_hash' not in payload
    assert 'subscriber_contact' not in payload
    assert 'audit_log' not in payload


def test_statistics_without_notifications_cannot_read_contact_delivery_data(client):
    secret_address = 'parent-private@example.test'
    with application.app.app_context():
        group = add_group('Statistics Only', {'statistics': 'full'})
        add_user('statsonly', group)
        application.db.session.add(application.NotificationLog(
            channel='email', recipient_name='Private Parent',
            recipient_address=secret_address, status='sent'))
        application.db.session.commit()
    login(client, 'statsonly', 'Another-Safe-Password')
    page = client.get('/admin/statistics')
    assert page.status_code == 200
    assert secret_address not in page.get_data(as_text=True)
    export = client.get('/admin/statistics/export/csv')
    assert export.status_code == 200
    assert secret_address not in export.get_data(as_text=True)
    denied_export = client.get('/admin/statistics/export/notifications')
    assert denied_export.status_code == 302
    assert denied_export.headers['Location'].endswith('/admin/dashboard')
    reset = client.post('/admin/statistics/reset', data={
        '_csrf': csrf_token(client), 'preset': 'today',
        'include_notifications': '1',
    })
    assert reset.status_code == 403


def test_csv_formula_cells_are_neutralized():
    for value in ('=1+1', '+cmd', '-2+3', '@SUM(A1:A2)', ' \t=HYPERLINK("x")'):
        assert application._csv_safe_cell(value).startswith("'")
    assert application._csv_safe_cell('ordinary text') == 'ordinary text'
    assert application._csv_safe_cell(42) == 42


def test_full_backup_is_encrypted_and_versioned(logged_in_client):
    response = logged_in_client.get('/admin/config/export-json')
    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'application/octet-stream'
    assert b'password_hash' not in response.data
    plaintext = Fernet(os.environ['BACKUP_ENCRYPTION_KEY'].encode()).decrypt(response.data)
    document = json.loads(plaintext)
    assert document['format'] == 'bustrack-full-backup'
    assert document['version'] == 1
    assert 'user' in document['tables']


def test_smtp_live_test_never_falls_back_to_stored_password(logged_in_client):
    with application.app.app_context():
        cfg = application.get_config()
        cfg.mail_provider = 'custom'
        cfg.mail_server = 'smtp.example.test'
        cfg.mail_username = 'mailer'
        cfg.mail_password = 'stored-secret'
        application.db.session.commit()
    response = logged_in_client.post('/admin/config/test-email-live', json={
        'provider': 'custom', 'server': 'alternate.example.test', 'port': 587,
        'username': 'mailer', 'password': '', 'test_to': 'recipient@example.test',
    }, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert response.status_code == 400
    assert 'Enter the SMTP password' in response.get_json()['message']


def test_smtp_connection_change_requires_password_reentry(logged_in_client):
    with application.app.app_context():
        cfg = application.get_config()
        cfg.mail_provider = 'custom'
        cfg.mail_server = 'smtp.example.test'
        cfg.mail_port = 587
        cfg.mail_username = 'mailer'
        cfg.mail_password = 'stored-secret'
        application.db.session.commit()
    response = logged_in_client.post('/admin/config', data={
        '_csrf': csrf_token(logged_in_client), 'section': 'email',
        'mail_provider': 'custom', 'mail_server': 'alternate.example.test',
        'mail_port': '587', 'mail_use_tls': 'on', 'mail_username': 'mailer',
        'mail_password': '',
    })
    assert response.status_code == 302
    with application.app.app_context():
        cfg = application.get_config()
        assert cfg.mail_server == 'smtp.example.test'
        assert cfg.mail_password == 'stored-secret'


def test_restore_failure_is_atomic_and_preserves_existing_data(logged_in_client):
    with application.app.app_context():
        document = application._full_backup_document()
        original_name = application.get_config().app_name
        duplicate = dict(document['tables']['incident_type'][0])
        document['tables']['incident_type'].append(duplicate)
        encrypted = application._backup_fernet().encrypt(json.dumps(
            document, default=application._json_default).encode())
    staged = logged_in_client.post('/admin/config/import-db', data={
        '_csrf': csrf_token(logged_in_client),
        'backup_file': (io.BytesIO(encrypted), 'backup.bustrack'),
    }, content_type='multipart/form-data')
    assert staged.status_code == 200
    job_id = staged.get_json()['job_id']
    restored = logged_in_client.post(
        f'/admin/config/import-run/{job_id}', json={},
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert restored.status_code == 400
    assert restored.get_json()['ok'] is False
    with application.app.app_context():
        assert application.get_config().app_name == original_name


def test_restore_rejects_backup_without_active_administrator(logged_in_client):
    with application.app.app_context():
        document = application._full_backup_document()
        for user in document['tables']['user']:
            user['active'] = False
        encrypted = application._backup_fernet().encrypt(json.dumps(
            document, default=application._json_default).encode())
    response = logged_in_client.post('/admin/config/import-db', data={
        '_csrf': csrf_token(logged_in_client),
        'backup_file': (io.BytesIO(encrypted), 'backup.bustrack'),
    }, content_type='multipart/form-data')
    assert response.status_code == 400
    assert response.get_json()['ok'] is False


def test_valid_full_backup_restores_successfully(logged_in_client):
    with application.app.app_context():
        original_name = application.get_config().app_name
        document = application._full_backup_document()
        encrypted = application._backup_fernet().encrypt(json.dumps(
            document, default=application._json_default).encode())
        application.get_config().app_name = 'Temporary Mutation'
        application.db.session.commit()
    staged = logged_in_client.post('/admin/config/import-db', data={
        '_csrf': csrf_token(logged_in_client),
        'backup_file': (io.BytesIO(encrypted), 'backup.bustrack'),
    }, content_type='multipart/form-data')
    assert staged.status_code == 200
    restored = logged_in_client.post(
        f"/admin/config/import-run/{staged.get_json()['job_id']}", json={},
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert restored.status_code == 200, restored.get_json()
    assert restored.get_json()['ok'] is True
    with application.app.app_context():
        assert application.get_config().app_name == original_name


def test_stored_user_data_is_not_emitted_as_executable_markup(logged_in_client):
    payload = '</script><img src=x onerror=alert(1)>'
    with application.app.app_context():
        admin = application.User.query.filter_by(username='admin').one()
        admin.first_name = payload
        application.db.session.commit()
    response = logged_in_client.get('/admin/users')
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert payload not in body
    assert '\\u003c/script\\u003e' in body


def test_dashboard_and_statistics_json_escape_script_terminators(logged_in_client):
    payload = '</script><script>alert(1)</script>'
    with application.app.app_context():
        incident = application.IncidentType(
            name=payload, color='#112233', icon='fa-circle', is_default=False)
        bus = application.Bus(identifier='XSS1', name=payload, route=payload, active=True)
        application.db.session.add_all([incident, bus])
        application.db.session.flush()
        application.db.session.add(application.BusIncidentRecord(
            bus_id=bus.id, incident_type_id=incident.id,
            incident_date=date.today(), is_pending=False))
        application.db.session.commit()
    for url in ('/admin/dashboard', '/admin/statistics'):
        response = logged_in_client.get(url)
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert payload not in body
        assert '\\u003c/script\\u003e' in body


def test_notification_and_holiday_payloads_remain_inert(logged_in_client):
    payload = "'><img src=x onerror=alert(1)>"
    with application.app.app_context():
        subscriber = application.NotificationSubscriber(notes=payload, active=True)
        application.db.session.add(subscriber)
        application.db.session.flush()
        application.db.session.add(application.SubscriberContact(
            subscriber_id=subscriber.id, first_name=payload, role='parent',
            email=payload, sort_order=0))
        application.db.session.add(application.Holiday(
            name=payload, holiday_date=date.today(), holiday_type='school',
            custom_message=payload, is_active=True))
        application.db.session.commit()
    for url in ('/admin/notifications', '/admin/config?tab=operational'):
        response = logged_in_client.get(url)
        assert response.status_code == 200
        assert payload not in response.get_data(as_text=True)


def test_svg_uploads_are_not_allowed():
    assert application.allowed_file('logo.svg') is False
    assert application.allowed_file('logo.png') is True


def test_verified_image_upload_is_stored_with_server_generated_name(logged_in_client):
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
    response = logged_in_client.post('/admin/config/upload-logo', data={
        '_csrf': csrf_token(logged_in_client), 'field': 'logo',
        'file': (io.BytesIO(png), 'operator-name.png'),
    }, content_type='multipart/form-data')
    assert response.status_code == 302
    with application.app.app_context():
        stored_path = application.get_config().logo_path
    assert stored_path.startswith('/static/uploads/app_logo_')
    assert stored_path.endswith('.png')
    local_path = Path(application.app.config['UPLOAD_FOLDER']) / Path(stored_path).name
    assert local_path.read_bytes() == png
    assert oct(local_path.stat().st_mode & 0o777) == '0o600'


def test_docker_build_context_cannot_copy_runtime_secrets():
    root = Path(__file__).resolve().parents[1]
    dockerignore = (root / '.dockerignore').read_text()
    dockerfile = (root / 'Dockerfile').read_text()
    compose = (root / 'docker-compose.yml').read_text()
    assert '\n.env\n' in dockerignore
    assert '\n*.env\n' in dockerignore
    assert 'static/uploads/' in dockerignore
    assert 'static/exports/' in dockerignore
    assert 'COPY . .' not in dockerfile
    assert 'SECRET_KEY:-changeme' not in compose
    assert 'DB_PASS:-buspass' not in compose
