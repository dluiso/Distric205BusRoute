import smtplib
import re

import app as application
from conftest import csrf_token
from email_service import EmailTransportError, classify_transport_error


def _configure_custom_email(password='saved-secret'):
    cfg = application.get_config()
    cfg.mail_provider = 'custom'
    cfg.mail_server = 'smtp.example.test'
    cfg.mail_port = 587
    cfg.mail_use_tls = True
    cfg.mail_use_ssl = False
    cfg.mail_username = 'mailer@example.test'
    cfg.mail_password = application._encrypt_mail_password(password)
    cfg.mail_from_email = 'mailer@example.test'
    cfg.mail_from_name = 'District Mailer'
    application.db.session.commit()
    return cfg


def test_live_test_reuses_encrypted_password_for_exact_saved_identity(
        logged_in_client, monkeypatch):
    with application.app.app_context():
        _configure_custom_email()

    observed = {}

    def fake_send(settings, **kwargs):
        observed['password'] = settings.password
        observed['server'] = settings.server
        observed['recipients'] = kwargs['recipients']

    monkeypatch.setattr(application, 'send_email', fake_send)
    response = logged_in_client.post('/admin/config/test-email-live', json={
        'provider': 'custom',
        'server': 'smtp.example.test',
        'port': 587,
        'use_tls': True,
        'use_ssl': False,
        'username': 'mailer@example.test',
        'password': '',
        'from_email': 'mailer@example.test',
        'from_name': 'District Mailer',
        'test_to': 'recipient@example.test',
    }, headers={'X-CSRF-Token': csrf_token(logged_in_client)})

    assert response.status_code == 200
    assert observed == {
        'password': 'saved-secret',
        'server': 'smtp.example.test',
        'recipients': ['recipient@example.test'],
    }
    with application.app.app_context():
        cfg = application.get_config()
        assert cfg.mail_last_verification_status == 'delivery_verified'
        assert application._mail_configuration_status(cfg)['verified'] is True


def test_office365_save_normalizes_transport_and_encrypts_password(logged_in_client):
    response = logged_in_client.post('/admin/config', data={
        '_csrf': csrf_token(logged_in_client),
        'section': 'email',
        'mail_provider': 'office365',
        'mail_server': 'smtp.office365.com',
        'mail_port': '465',
        'mail_use_tls': 'on',
        'mail_username': 'transport@example.test',
        'mail_password': 'not-a-real-production-password',
        'mail_from_email': 'transport@example.test',
        'mail_from_name': 'District Transport',
    })

    assert response.status_code == 302
    with application.app.app_context():
        cfg = application.get_config()
        assert cfg.mail_server == 'smtp.office365.com'
        assert cfg.mail_port == 587
        assert cfg.mail_use_tls is True
        assert cfg.mail_use_ssl is False
        assert cfg.mail_password.startswith('enc:v1:')
        assert application._decrypt_mail_password(cfg.mail_password) == (
            'not-a-real-production-password')
        assert cfg.mail_last_verification_status == 'unverified'


def test_email_page_renders_canonical_preset_and_real_verification_status(
        logged_in_client):
    with application.app.app_context():
        cfg = application.get_config()
        cfg.mail_provider = 'office365'
        cfg.mail_server = 'smtp.office365.com'
        cfg.mail_port = 465
        cfg.mail_use_tls = True
        cfg.mail_use_ssl = False
        cfg.mail_username = 'transport@example.test'
        cfg.mail_password = application._encrypt_mail_password('saved-secret')
        cfg.mail_from_email = 'transport@example.test'
        application.db.session.commit()

    response = logged_in_client.get('/admin/config?tab=email')
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert re.search(r'id="mail_port" value="587"', body)
    assert 'Saved; verification required' in body
    assert 'Ready to send' not in body


def test_migration_command_encrypts_legacy_secret_and_normalizes_office365():
    with application.app.app_context():
        cfg = application.get_config()
        cfg.mail_provider = 'office365'
        cfg.mail_server = 'smtp.office365.com'
        cfg.mail_port = 465
        cfg.mail_use_tls = True
        cfg.mail_use_ssl = False
        cfg.mail_username = 'transport@example.test'
        cfg.mail_password = 'legacy-plaintext-value'
        cfg.mail_from_email = 'transport@example.test'
        application.db.session.commit()

    result = application.app.test_cli_runner().invoke(args=['migrate-email-config'])
    assert result.exit_code == 0, result.output
    assert 'credential_encrypted' in result.output
    assert 'transport_normalized' in result.output
    with application.app.app_context():
        cfg = application.get_config()
        assert cfg.mail_port == 587
        assert cfg.mail_use_tls is True
        assert cfg.mail_use_ssl is False
        assert application._decrypt_mail_password(cfg.mail_password) == (
            'legacy-plaintext-value')


def test_outbox_retries_transient_failure_then_marks_delivery_sent(monkeypatch):
    with application.app.app_context():
        _configure_custom_email()
        row = application._enqueue_email(
            dedupe_key='test:transient-delivery',
            kind='test',
            recipient_name='Test Parent',
            recipient_address='parent@example.test',
            subject='Bus update',
            body='A durable test message.',
        )
        application.db.session.commit()
        row_id = row.id

    calls = {'count': 0}

    def transient_then_success(settings, **kwargs):
        calls['count'] += 1
        if calls['count'] == 1:
            raise EmailTransportError(
                'connection_timeout', 'The SMTP connection timed out.', retryable=True)

    monkeypatch.setattr(application, 'send_email', transient_then_success)
    application.process_email_outbox()
    with application.app.app_context():
        row = application.db.session.get(application.EmailOutbox, row_id)
        assert row.status == 'retry'
        assert row.attempts == 1
        assert row.last_error_code == 'connection_timeout'
        row.available_at = application._utcnow()
        application.db.session.commit()

    application.process_email_outbox()
    with application.app.app_context():
        row = application.db.session.get(application.EmailOutbox, row_id)
        assert row.status == 'sent'
        assert row.attempts == 2
        assert row.sent_at is not None
        assert row.last_error_code == ''


def test_transport_error_classification_preserves_smtp_response_semantics():
    temporary = classify_transport_error(smtplib.SMTPDataError(451, b'try later'))
    rejected = classify_transport_error(smtplib.SMTPDataError(550, b'blocked'))
    disconnected = classify_transport_error(smtplib.SMTPServerDisconnected('gone'))

    assert (temporary.code, temporary.retryable) == ('smtp_temporary_failure', True)
    assert (rejected.code, rejected.retryable) == ('smtp_rejected', False)
    assert (disconnected.code, disconnected.retryable) == ('server_disconnected', True)
