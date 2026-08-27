import io
import json
import os
from datetime import timedelta
from pathlib import Path
from sqlalchemy import inspect

import app as application
from conftest import csrf_token, login
from test_phase1_security import add_group, add_user


def grant_derived_capabilities(group):
    application._sync_group_capabilities(group.id)
    application.db.session.commit()


def test_explicit_capabilities_preserve_modules_without_granting_admin_boundaries(client):
    with application.app.app_context():
        group = add_group('Configuration Operators', {'config': 'full'})
        grant_derived_capabilities(group)
        user = add_user('config-operator', group)
        assert user.has_capability('config.write')
        assert user.has_capability('backup.export_operational')
        assert user.has_capability('smtp.diagnose')
        assert not user.has_capability('backup.export_sensitive')
        assert not user.has_capability('restore.identity')
        assert not user.has_capability('user.assign_admin')
    login(client, 'config-operator', 'Another-Safe-Password')
    assert client.get('/admin/config/export-operational-json').status_code == 200
    assert client.get('/admin/config/export-json').status_code == 403


def test_startup_seed_preserves_existing_explicit_capability_deny():
    with application.app.app_context():
        group = add_group('PII Denied Operators', {'notifications': 'full'})
        grant_derived_capabilities(group)
        deny = application.GroupCapability.query.filter_by(
            group_id=group.id, capability_key='notifications.export_pii').one()
        deny.granted = False
        application.db.session.commit()
        application._seed_phase2_security_and_imports()
        assert application.GroupCapability.query.filter_by(
            group_id=group.id,
            capability_key='notifications.export_pii').one().granted is False


def test_limited_notifications_masks_pii_and_blocks_export(client):
    secret_email = 'private.parent@example.test'
    secret_phone = '+17085551234'
    with application.app.app_context():
        group = add_group('Notification Readers', {'notifications': 'limited'})
        add_user('notification-reader', group)
        subscriber = application.NotificationSubscriber(notes='Private Household')
        application.db.session.add(subscriber)
        application.db.session.flush()
        application.db.session.add(application.SubscriberContact(
            subscriber_id=subscriber.id, first_name='Private', last_name='Parent',
            email=secret_email, phone=secret_phone, role='parent'))
        application.db.session.commit()
    login(client, 'notification-reader', 'Another-Safe-Password')
    page = client.get('/admin/notifications')
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert secret_email not in body
    assert secret_phone not in body
    assert 'p***@example.test' in body
    assert '***-***-1234' in body
    assert client.get('/admin/notifications/export-csv').status_code == 403


def test_security_headers_and_post_only_logout(logged_in_client):
    response = logged_in_client.get('/admin/dashboard')
    assert response.headers['Cache-Control'].startswith('no-store')
    assert "default-src 'self'" in response.headers['Content-Security-Policy']
    assert 'unpkg.com' not in response.headers['Content-Security-Policy']
    assert response.headers['Cross-Origin-Opener-Policy'] == 'same-origin'
    assert logged_in_client.get('/admin/logout').status_code == 405
    logged_out = logged_in_client.post('/admin/logout', data={
        '_csrf': csrf_token(logged_in_client),
    })
    assert logged_out.status_code == 302
    assert logged_in_client.get('/admin/dashboard').status_code == 302


def test_admin_ui_has_no_eval_dependent_alpine_runtime():
    root = Path(__file__).resolve().parents[1]
    base = (root / 'templates/admin/base.html').read_text(encoding='utf-8')
    notifications = (root / 'templates/admin/notifications.html').read_text(
        encoding='utf-8')
    combined = base + notifications
    for marker in ('alpinejs', 'x-data=', 'x-for=', 'x-model=', '@click='):
        assert marker not in combined
    assert 'id="sidebar-toggle"' in base
    assert 'id="contact-row-template"' in notifications
    assert 'setContactEditorContacts' in notifications


def test_broadcast_status_is_database_backed_owner_bound_and_expires(client):
    with application.app.app_context():
        owner = application.User.query.filter_by(username='admin').one()
        now = application._utcnow()
        application.db.session.add(application.BroadcastJob(
            public_id='durable-job', owner_id=owner.id, status='running',
            total=3, sent=1, failed=1, errors_json=json.dumps(['p***: delivery failed']),
            created_at=now, updated_at=now, expires_at=now + timedelta(hours=1)))
        application.db.session.commit()
    login(client)
    response = client.get('/admin/notifications/broadcast/durable-job/status')
    assert response.status_code == 200
    assert response.get_json() == {
        'done': False, 'errors': ['p***: delivery failed'], 'failed': 1,
        'sent': 1, 'status': 'running', 'total': 3,
    }


def test_legacy_csv_uses_immutable_stage_plan_hash_and_is_idempotent(logged_in_client):
    with application.app.app_context():
        application.db.session.add(application.Bus(
            identifier='TEST', name='01', route='Test Route', active=True))
        application.db.session.commit()
    csv_data = (
        'schema_version,subscriber_id,household_label,group,active,role,'
        'first_name,last_name,email,phone\n'
        'Legacy CSV v1,,House One,TEST 01,yes,parent,Ada,Lovelace,'
        'ADA@EXAMPLE.TEST,17085550101\n'
    ).encode()
    preview = logged_in_client.post('/admin/notifications/import-csv/preview', data={
        '_csrf': csrf_token(logged_in_client),
        'csv_file': (io.BytesIO(csv_data), 'legacy.csv', 'text/csv'),
    }, content_type='multipart/form-data')
    assert preview.status_code == 200, preview.get_data(as_text=True)
    report = preview.get_json()
    assert report['ok'] and report['can_import']
    assert report['schema_version'] == 'Legacy CSV v1'
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        row = application.ImportRow.query.filter_by(batch_id=batch.id).one()
        normalized = json.loads(row.normalized_json)
        assert normalized['email'] == 'ada@example.test'
        assert normalized['phone'] == '+17085550101'
        staged_file = application.ImportFile.query.filter_by(batch_id=batch.id).one()
        staged_path = Path(staged_file.storage_path)
        assert staged_path.stat().st_mode & 0o777 == 0o600

    tampered = logged_in_client.post('/admin/notifications/import-csv', data={
        '_csrf': csrf_token(logged_in_client), 'batch_id': report['batch_id'],
        'plan_hash': '0' * 64,
    })
    assert tampered.status_code == 409
    committed = logged_in_client.post('/admin/notifications/import-csv', data={
        '_csrf': csrf_token(logged_in_client), 'batch_id': report['batch_id'],
        'plan_hash': report['plan_hash'],
    })
    assert committed.status_code == 302
    with application.app.app_context():
        assert application.NotificationSubscriber.query.count() == 1
        assert application.SubscriberContact.query.one().email == 'ada@example.test'
        assert application.ImportChange.query.count() == 1
        assert application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one().status == 'applied'
        assert application.ImportFile.query.filter_by(batch_id=batch.id).count() == 0
        assert not staged_path.exists()
    repeated = logged_in_client.post('/admin/notifications/import-csv', data={
        '_csrf': csrf_token(logged_in_client), 'batch_id': report['batch_id'],
        'plan_hash': report['plan_hash'],
    })
    assert repeated.status_code == 302
    with application.app.app_context():
        assert application.NotificationSubscriber.query.count() == 1


def test_import_schema_is_additive_and_powerschool_flag_defaults_off():
    with application.app.app_context():
        tables = set(inspect(application.db.engine).get_table_names())
        assert {
            'import_mapping_profile', 'import_batch', 'import_file', 'import_row',
            'external_identity', 'import_change',
        }.issubset(tables)
        profile = application.ImportMappingProfile.query.filter_by(
            source_type='powerschool', schema_version='1').one()
        assert profile.name == 'PowerSchool Import v1'
        assert application.app.config['POWERSCHOOL_IMPORT_ENABLED'] is False


def test_phase1_backup_version_remains_accepted():
    with application.app.app_context():
        current = application._full_backup_document()
        legacy = {
            'format': current['format'], 'version': 1,
            'tables': {name: current['tables'][name]
                       for name in application._IMPORT_TABLE_ORDER_V1},
        }
        validated = application._validate_backup_document(legacy)
        assert [name for name, _ in validated] == application._IMPORT_TABLE_ORDER_V1


def test_phase1_backup_restores_after_phase2_tables_exist(logged_in_client):
    with application.app.app_context():
        current = application._full_backup_document()
        legacy = {
            'format': current['format'], 'version': 1,
            'tables': {name: current['tables'][name]
                       for name in application._IMPORT_TABLE_ORDER_V1},
        }
        legacy['tables']['configuration'][0]['mail_password'] = 'legacy-mail-secret'
        owner = application.User.query.filter_by(username='admin').one()
        now = application._utcnow()
        job = application.BroadcastJob(
            public_id='pre-v1-restore-job', owner_id=owner.id, status='completed',
            total=1, sent=1, failed=0, errors_json='[]', created_at=now,
            updated_at=now, expires_at=now + timedelta(hours=1))
        application.db.session.add(job)
        application.db.session.add(application.EmailOutbox(
            dedupe_key='pre-v1-restore-outbox', kind='broadcast',
            recipient_address='parent@example.test', subject='Old', body='Old',
            status='sent', available_at=now, broadcast_job_id=job.public_id))
        application.db.session.commit()
        encrypted = application._backup_fernet().encrypt(json.dumps(
            legacy, default=application._json_default).encode())
    staged = logged_in_client.post('/admin/config/import-db', data={
        '_csrf': csrf_token(logged_in_client),
        'backup_file': (io.BytesIO(encrypted), 'legacy.bustrack'),
    }, content_type='multipart/form-data')
    assert staged.status_code == 200, staged.get_data(as_text=True)
    restored = logged_in_client.post(
        f"/admin/config/import-run/{staged.get_json()['job_id']}", json={},
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert restored.status_code == 200, restored.get_data(as_text=True)
    with application.app.app_context():
        assert application.BroadcastJob.query.count() == 0
        assert application.EmailOutbox.query.count() == 0
        cfg = application.get_config()
        assert cfg.mail_password.startswith('enc:v1:')
        assert application._decrypt_mail_password(cfg.mail_password) == 'legacy-mail-secret'
        assert application.ImportMappingProfile.query.filter_by(
            source_type='legacy_csv', schema_version='1').one()


def test_all_templates_compile():
    with application.app.app_context():
        for template_name in application.app.jinja_env.list_templates():
            application.app.jinja_env.get_template(template_name)


def test_docker_image_packages_shared_safe_output_module():
    dockerfile = (Path(__file__).resolve().parents[1] / 'Dockerfile').read_text()
    assert 'COPY --chown=1000:1000 static/js ./static/js' in dockerfile
    assert 'COPY --chown=1000:1000 powerschool_import.py ./powerschool_import.py' in dockerfile
    assert 'COPY --chown=1000:1000 static/templates ./static/templates' in dockerfile


def test_dependency_versions_are_pinned():
    root = Path(__file__).resolve().parents[1]
    for line in (root / 'requirements.txt').read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            assert '==' in line and '>=' not in line
