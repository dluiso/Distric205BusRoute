import json
import re
from pathlib import Path

import app as application


def _bulk_token(response):
    match = re.search(r'"bulkToken":\s*"([0-9a-f]{48})"', response.get_data(as_text=True))
    assert match
    return match.group(1)


def _csrf_headers(client):
    with client.session_transaction() as session:
        return {'X-CSRF-Token': session['_csrf']}


def _seed_bus(label='01'):
    bus = application.Bus(identifier='TT', name=label, route=f'Route {label}', active=True)
    application.db.session.add(bus)
    application.db.session.commit()
    return bus.id


def _pending_record(bus_id, *, schedule_type_id=None):
    delayed = application.IncidentType.query.filter_by(name='Delayed').one()
    record = application.BusIncidentRecord(
        bus_id=bus_id, incident_type_id=delayed.id,
        schedule_type_id=schedule_type_id,
        incident_date=application.district_today(), delay_minutes=12,
        delay_reason_text='Traffic', is_pending=True,
    )
    application.db.session.add(record)
    application.db.session.commit()
    return record.id


def test_live_operations_endpoint_is_private_conditional_and_pii_free(logged_in_client):
    with application.app.app_context():
        bus_id = _seed_bus()
        group = application.SubscriberGroup(name='TT 01 AM')
        application.db.session.add(group)
        application.db.session.flush()
        application.db.session.add(application.GroupBusAssignment(
            group_id=group.id, bus_id=bus_id))
        subscriber = application.NotificationSubscriber(
            group_id=group.id, school='155', active=True, notes='Private Family')
        application.db.session.add(subscriber)
        application.db.session.flush()
        application.db.session.add(application.SubscriberContact(
            subscriber_id=subscriber.id, first_name='Private', last_name='Parent',
            email='private@example.test', phone='+12195550101',
            role='parent', preferred_language='es'))
        application.db.session.commit()

    response = logged_in_client.get('/admin/dashboard/operations.json')
    assert response.status_code == 200
    assert 'private' in response.headers['Cache-Control']
    assert response.headers.get('ETag')
    payload = response.get_json()
    assert payload['buses'][0]['school_names'] == ['155']
    serialized = json.dumps(payload)
    assert 'private@example.test' not in serialized
    assert 'Private Family' not in serialized
    conditional = logged_in_client.get(
        '/admin/dashboard/operations.json',
        headers={'If-None-Match': response.headers['ETag']})
    assert conditional.status_code == 304


def test_recipient_preview_is_deduplicated_aggregate_only(logged_in_client):
    with application.app.app_context():
        bus_id = _seed_bus()
        group = application.SubscriberGroup(name='TT 01 AM')
        application.db.session.add(group)
        application.db.session.flush()
        application.db.session.add(application.GroupBusAssignment(
            group_id=group.id, bus_id=bus_id))
        subscriber = application.NotificationSubscriber(
            group_id=group.id, school='155', active=True)
        application.db.session.add(subscriber)
        application.db.session.flush()
        application.db.session.add_all([
            application.SubscriberContact(
                subscriber_id=subscriber.id, first_name='Pat',
                email='same@example.test', phone='+12195550101',
                role='parent', preferred_language='es', sort_order=0),
            application.SubscriberContact(
                subscriber_id=subscriber.id, first_name='Student',
                email='same@example.test', phone='+12195550102',
                role='student', preferred_language='en', sort_order=1),
        ])
        application.db.session.commit()

    response = logged_in_client.post(
        '/admin/dashboard/recipients/preview', json={'bus_ids': [bus_id]},
        headers=_csrf_headers(logged_in_client))
    assert response.status_code == 200
    preview = response.get_json()['preview']
    assert preview['subscriber_count'] == 1
    assert preview['contact_count'] == 2
    assert preview['email_count'] == 1
    assert preview['sms_count'] == 2
    assert preview['roles'] == {'parent': 1, 'student': 1}
    assert preview['languages'] == {'en': 1, 'es': 1}
    assert preview['schools'] == {'155': 1}
    serialized = json.dumps(response.get_json())
    assert 'same@example.test' not in serialized
    assert '+12195550101' not in serialized
    assert 'Pat' not in serialized


def test_pending_confirmation_is_single_winner_and_creates_neutral_event(
        logged_in_client, monkeypatch):
    monkeypatch.setattr(application, '_send_bus_notifications', lambda _record: None)
    with application.app.app_context():
        bus_id = _seed_bus()
        record_id = _pending_record(bus_id)
        record = application.db.session.get(application.BusIncidentRecord, record_id)
        version = f'{record.id}:{record.updated_at.isoformat() if record.updated_at else ""}'

    first = logged_in_client.post(
        f'/admin/dashboard/incidents/{record_id}/confirm', json={'version': version},
        headers=_csrf_headers(logged_in_client))
    second = logged_in_client.post(
        f'/admin/dashboard/incidents/{record_id}/confirm', json={'version': version},
        headers=_csrf_headers(logged_in_client))
    assert first.status_code == 200
    assert second.status_code == 409
    with application.app.app_context():
        record = application.db.session.get(application.BusIncidentRecord, record_id)
        assert record.is_pending is False
        event = application.CommunicationEvent.query.filter_by(
            incident_record_id=record_id).one()
        assert event.event_type == 'bus_status_committed'
        assert 'email' not in event.payload_json.lower()
        assert application.AuditLog.query.filter_by(
            action='confirm_bus_incident').count() == 1


def test_pending_cancel_requires_current_version_and_is_audited(logged_in_client):
    with application.app.app_context():
        bus_id = _seed_bus()
        record_id = _pending_record(bus_id)
        record = application.db.session.get(application.BusIncidentRecord, record_id)
        version = f'{record.id}:{record.updated_at.isoformat() if record.updated_at else ""}'

    stale = logged_in_client.post(
        f'/admin/dashboard/incidents/{record_id}/cancel', json={'version': 'stale'},
        headers=_csrf_headers(logged_in_client))
    assert stale.status_code == 409
    cancelled = logged_in_client.post(
        f'/admin/dashboard/incidents/{record_id}/cancel', json={'version': version},
        headers=_csrf_headers(logged_in_client))
    assert cancelled.status_code == 200
    with application.app.app_context():
        assert application.db.session.get(application.BusIncidentRecord, record_id) is None
        assert application.AuditLog.query.filter_by(
            action='cancel_bus_incident').count() == 1


def test_pending_queue_includes_return_to_on_time(logged_in_client):
    with application.app.app_context():
        bus_id = _seed_bus()
        on_time = application.IncidentType.query.filter_by(is_default=True).one()
        record = application.BusIncidentRecord(
            bus_id=bus_id, incident_type_id=on_time.id,
            incident_date=application.district_today(), is_pending=True)
        application.db.session.add(record)
        application.db.session.commit()
        record_id = record.id
    payload = logged_in_client.get('/admin/dashboard/operations.json').get_json()
    assert payload['pending_count'] == 1
    assert [item['id'] for item in payload['pending_queue']] == [record_id]
    assert payload['pending_queue'][0]['is_default'] is True


def test_bulk_status_update_is_idempotent_and_all_or_nothing(logged_in_client):
    with application.app.app_context():
        first_id = _seed_bus('01')
        second_id = _seed_bus('02')
        delayed_id = application.IncidentType.query.filter_by(name='Delayed').one().id
    token = _bulk_token(logged_in_client.get('/admin/dashboard'))
    payload = {
        'bus_ids': [first_id, second_id], 'confirmed': True,
        'request_token': token,
        'expected_latest_ids': {str(first_id): 0, str(second_id): 0},
        'incident': {
            'incident_type_id': delayed_id, 'schedule_type_id': '',
            'delay_minutes': 10, 'eta': '', 'delay_reason_id': '',
            'delay_reason_text': 'Weather', 'notes': 'Bulk test',
        },
    }
    first = logged_in_client.post('/admin/dashboard/incidents/bulk', json=payload,
                                  headers=_csrf_headers(logged_in_client))
    duplicate = logged_in_client.post('/admin/dashboard/incidents/bulk', json=payload,
                                      headers=_csrf_headers(logged_in_client))
    assert first.status_code == 200
    assert first.get_json()['created'] == 2
    assert duplicate.status_code == 200
    assert duplicate.get_json()['duplicate'] is True
    with application.app.app_context():
        records = application.BusIncidentRecord.query.order_by(
            application.BusIncidentRecord.bus_id).all()
        assert len(records) == 2
        assert all(record.is_pending for record in records)
        assert application.AuditLog.query.filter_by(
            action='bulk_add_bus_incident').count() == 1


def test_bulk_status_update_rejects_stale_selection_without_partial_rows(
        logged_in_client):
    with application.app.app_context():
        first_id = _seed_bus('01')
        second_id = _seed_bus('02')
        current_id = _pending_record(first_id)
        delayed_id = application.IncidentType.query.filter_by(name='Delayed').one().id
    token = _bulk_token(logged_in_client.get('/admin/dashboard'))
    response = logged_in_client.post(
        '/admin/dashboard/incidents/bulk',
        headers=_csrf_headers(logged_in_client),
        json={
            'bus_ids': [first_id, second_id], 'confirmed': True,
            'request_token': token,
            'expected_latest_ids': {str(first_id): 0, str(second_id): 0},
            'incident': {
                'incident_type_id': delayed_id, 'schedule_type_id': '',
                'delay_minutes': 5, 'eta': '', 'delay_reason_id': '',
                'delay_reason_text': '', 'notes': '',
            },
        },
    )
    assert response.status_code == 409
    assert response.get_json()['conflict_count'] == 1
    with application.app.app_context():
        assert application.BusIncidentRecord.query.count() == 1
        assert application.db.session.get(
            application.BusIncidentRecord, current_id) is not None
        assert application.BusIncidentRecord.query.filter_by(
            bus_id=second_id).count() == 0


def test_dashboard_phase_three_and_four_ui_contracts():
    root = Path(__file__).resolve().parents[1]
    template = (root / 'templates/admin/dashboard.html').read_text(encoding='utf-8')
    script = (root / 'static/js/admin_dashboard.js').read_text(encoding='utf-8')
    notifications = (root / 'templates/admin/notifications.html').read_text(encoding='utf-8')
    for contract in (
        'dashboard-refresh', 'dashboard-pending-section', 'dashboard-bulk-bar',
        'dashboard-bulk-modal', 'dashboard-route-filter', 'dashboard-group-filter',
        'dashboard-school-filter', 'dashboard-saved-view',
        'dashboard-filter-toggle', 'dashboard-filter-panel',
        'dashboard-filter-count', 'dashboard-operations-header',
        'drawer-preview-recipients', 'dashboard-toast-region',
        "static_asset_version('js/admin_dashboard.js')",
    ):
        assert contract in template
    for contract in (
        'If-None-Match', 'document.hidden', 'visibilitychange',
        'setInterval(() => refreshOperations(false), 25000)',
        'localStorage', 'expected_latest_ids', 'data-pending-until',
        "filterPanel?.classList.toggle('is-open', open)",
    ):
        assert contract in script
    assert 'data-contact-field="preferred_language"' in notifications
    assert 'edit-subscriber-school' in notifications


def test_static_asset_version_changes_cache_key_without_path_escape():
    version = application.static_asset_version('js/admin_dashboard.js')
    assert version.isdigit()
    assert version != '0'
    assert application.static_asset_version('../app.py') == '0'
