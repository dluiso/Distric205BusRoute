from datetime import timedelta
from pathlib import Path

from sqlalchemy import event

import app as application
from conftest import csrf_token, login


def _bus(identifier='TT', name='01', *, active=True, deleted_at=None):
    bus = application.Bus(
        identifier=identifier, name=name, route=f'Route {identifier} {name}',
        active=active, deleted_at=deleted_at,
    )
    application.db.session.add(bus)
    application.db.session.commit()
    return bus.id


def _post(client, path, data=None):
    return client.post(path, data={
        '_csrf': csrf_token(client), **(data or {}),
    }, follow_redirects=True)


def _csrf_headers(client):
    with client.session_transaction() as session:
        return {'X-CSRF-Token': session['_csrf']}


def _versions(bus_ids):
    return {
        str(bus_id): application._bus_lifecycle_version(
            application.db.session.get(application.Bus, bus_id))
        for bus_id in bus_ids
    }


def _bulk(client, bus_ids, action, *, reason='', versions=None):
    with application.app.app_context():
        expected_versions = versions or _versions(bus_ids)
    return client.post('/admin/buses/bulk-lifecycle', json={
        'bus_ids': bus_ids,
        'action': action,
        'reason': reason,
        'confirmed': True,
        'expected_versions': expected_versions,
    }, headers=_csrf_headers(client))


def test_inventory_renders_all_lifecycle_states_and_new_controls(logged_in_client):
    with application.app.app_context():
        _bus('TT', '01')
        _bus('TR', '02', active=False)
        _bus('AFL', '03', active=False, deleted_at=application._utcnow())

    response = logged_in_client.get('/admin/buses')
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'Bus Inventory' in body
    assert 'TT - 01' in body
    assert 'TR - 02' in body
    assert 'AFL - 03' in body
    assert 'id="bus-card-view"' in body
    assert 'id="bus-list-view"' in body
    assert 'data-state-summary="inactive"' in body
    assert 'data-state-summary="trash"' in body
    assert 'Search TT-01, TT 01' in body


def test_deactivate_activate_and_audit_are_explicit(logged_in_client):
    with application.app.app_context():
        bus_id = _bus()

    response = _post(logged_in_client, f'/admin/buses/{bus_id}/deactivate', {
        'reason': 'Seasonal route',
    })
    assert response.status_code == 200
    assert 'deactivated' in response.get_data(as_text=True)
    with application.app.app_context():
        bus = application.db.session.get(application.Bus, bus_id)
        assert bus.lifecycle_state == 'inactive'
        assert bus.deactivated_by == 'admin'
        assert bus.deactivation_reason == 'Seasonal route'
        assert application.AuditLog.query.filter_by(
            action='deactivate_bus', target='TT — 01').count() == 1

    response = _post(logged_in_client, f'/admin/buses/{bus_id}/activate')
    assert response.status_code == 200
    with application.app.app_context():
        bus = application.db.session.get(application.Bus, bus_id)
        assert bus.lifecycle_state == 'active'
        assert bus.deactivated_at is None
        assert bus.deactivated_by is None
        assert application.AuditLog.query.filter_by(
            action='activate_bus', target='TT — 01').count() == 1


def test_pending_work_blocks_deactivation_and_trash(logged_in_client):
    with application.app.app_context():
        bus_id = _bus()
        delayed = application.IncidentType.query.filter_by(name='Delayed').one()
        application.db.session.add(application.BusIncidentRecord(
            bus_id=bus_id, incident_type_id=delayed.id,
            incident_date=application.district_today(), is_pending=True,
        ))
        application.db.session.commit()

    deactivate = _post(
        logged_in_client, f'/admin/buses/{bus_id}/deactivate')
    trash = _post(logged_in_client, f'/admin/buses/{bus_id}/trash', {
        'reason': 'Remove from inventory',
    })
    assert 'pending' in deactivate.get_data(as_text=True).lower()
    assert 'pending' in trash.get_data(as_text=True).lower()
    with application.app.app_context():
        bus = application.db.session.get(application.Bus, bus_id)
        assert bus.lifecycle_state == 'active'


def test_trash_requires_reason_and_restore_is_safely_inactive(logged_in_client):
    with application.app.app_context():
        bus_id = _bus('TR', '10')

    missing_reason = _post(
        logged_in_client, f'/admin/buses/{bus_id}/trash')
    assert 'Enter a reason' in missing_reason.get_data(as_text=True)
    with application.app.app_context():
        assert application.db.session.get(application.Bus, bus_id).deleted_at is None

    moved = _post(logged_in_client, f'/admin/buses/{bus_id}/trash', {
        'reason': 'Duplicate entered by operator',
    })
    assert 'moved to Trash' in moved.get_data(as_text=True)
    with application.app.app_context():
        bus = application.db.session.get(application.Bus, bus_id)
        assert bus.lifecycle_state == 'trash'
        assert bus.deleted_by == 'admin'
        assert bus.deletion_reason == 'Duplicate entered by operator'

    restored = _post(logged_in_client, f'/admin/buses/{bus_id}/restore')
    assert 'restored as inactive' in restored.get_data(as_text=True)
    with application.app.app_context():
        bus = application.db.session.get(application.Bus, bus_id)
        assert bus.lifecycle_state == 'inactive'
        assert bus.deleted_at is None
        assert bus.active is False


def test_purge_is_admin_only_retained_and_dependency_safe(client, logged_in_client):
    old = application._utcnow() - timedelta(
        days=application.app.config['BUS_TRASH_RETENTION_DAYS'] + 1)
    with application.app.app_context():
        unused_id = _bus('OLD', '01', active=False, deleted_at=old)
        retained_id = _bus('OLD', '02', active=False, deleted_at=old)
        on_time = application.IncidentType.query.filter_by(is_default=True).one()
        application.db.session.add(application.BusIncidentRecord(
            bus_id=retained_id, incident_type_id=on_time.id,
            incident_date=application.district_today(), is_pending=False,
        ))
        group = application.UserGroup(name='Fleet Managers', is_admin=False)
        application.db.session.add(group)
        application.db.session.flush()
        application.db.session.add(application.GroupPermission(
            group_id=group.id, module_key='buses', access_level='full'))
        manager = application.User(username='fleet-manager', group_id=group.id, active=True)
        manager.set_password('Fleet-Manager-Password')
        application.db.session.add(manager)
        application.db.session.commit()

    manager_client = application.app.test_client()
    login(manager_client, 'fleet-manager', 'Fleet-Manager-Password')
    denied = manager_client.post(
        f'/admin/buses/{unused_id}/purge',
        data={'_csrf': csrf_token(manager_client)})
    assert denied.status_code == 403

    protected = _post(logged_in_client, f'/admin/buses/{retained_id}/purge')
    assert 'must remain archived' in protected.get_data(as_text=True)
    deleted = _post(logged_in_client, f'/admin/buses/{unused_id}/purge')
    assert 'permanently deleted' in deleted.get_data(as_text=True)
    with application.app.app_context():
        assert application.db.session.get(application.Bus, unused_id) is None
        assert application.db.session.get(application.Bus, retained_id) is not None


def test_inactive_bus_cannot_commit_incident_or_claim_outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(application, '_send_bus_notifications', sent.append)
    with application.app.app_context():
        bus_id = _bus(active=False)
        delayed = application.IncidentType.query.filter_by(name='Delayed').one()
        record = application.BusIncidentRecord(
            bus_id=bus_id, incident_type_id=delayed.id,
            incident_date=application.district_today(), is_pending=True,
        )
        application.db.session.add(record)
        application.db.session.flush()
        application.db.session.add(application.EmailOutbox(
            dedupe_key='inactive-bus-outbox', kind='incident',
            recipient_address='recipient@example.test', subject='Subject', body='Body',
            bus_id=bus_id, incident_record_id=record.id,
            available_at=application._utcnow(), status='pending',
        ))
        application.db.session.commit()
        record_id = record.id

        assert application._commit_pending_incident_once(record_id) is None
        assert application._claim_due_email_ids() == []
        assert application.db.session.get(
            application.BusIncidentRecord, record_id).is_pending is True
        assert sent == []


def test_bulk_lifecycle_is_atomic_audited_and_recoverable(logged_in_client):
    with application.app.app_context():
        first_id = _bus('TT', '21')
        second_id = _bus('TT', '22', active=False)

    trashed = _bulk(
        logged_in_client, [first_id, second_id], 'trash',
        reason='End of seasonal service')
    assert trashed.status_code == 200
    assert trashed.get_json()['count'] == 2
    with application.app.app_context():
        buses = [application.db.session.get(application.Bus, bus_id)
                 for bus_id in (first_id, second_id)]
        assert {bus.lifecycle_state for bus in buses} == {'trash'}
        assert all(bus.deletion_reason == 'End of seasonal service' for bus in buses)
        assert application.AuditLog.query.filter_by(
            action='bulk_trash_buses').count() == 1

    restored = _bulk(
        logged_in_client, [first_id, second_id], 'restore')
    assert restored.status_code == 200
    with application.app.app_context():
        buses = [application.db.session.get(application.Bus, bus_id)
                 for bus_id in (first_id, second_id)]
        assert {bus.lifecycle_state for bus in buses} == {'inactive'}
        assert application.AuditLog.query.filter_by(
            action='bulk_restore_buses').count() == 1


def test_bulk_lifecycle_blocks_pending_and_stale_selections_without_partial_change(
        logged_in_client):
    with application.app.app_context():
        first_id = _bus('TR', '31')
        second_id = _bus('TR', '32')
        stale_versions = _versions([first_id, second_id])
        delayed = application.IncidentType.query.filter_by(name='Delayed').one()
        application.db.session.add(application.BusIncidentRecord(
            bus_id=second_id, incident_type_id=delayed.id,
            incident_date=application.district_today(), is_pending=True,
        ))
        application.db.session.commit()

    blocked = _bulk(
        logged_in_client, [first_id, second_id], 'deactivate',
        reason='Temporary shutdown')
    assert blocked.status_code == 409
    assert blocked.get_json()['blocker_count'] == 1
    with application.app.app_context():
        assert all(application.db.session.get(
            application.Bus, bus_id).lifecycle_state == 'active'
                   for bus_id in (first_id, second_id))
        application.db.session.get(application.Bus, first_id).updated_at = (
            application._utcnow() + timedelta(seconds=1))
        application.db.session.commit()

    stale = _bulk(
        logged_in_client, [first_id, second_id], 'deactivate',
        reason='Temporary shutdown', versions=stale_versions)
    assert stale.status_code == 409
    assert stale.get_json()['conflict_count'] >= 1
    with application.app.app_context():
        assert all(application.db.session.get(
            application.Bus, bus_id).lifecycle_state == 'active'
                   for bus_id in (first_id, second_id))


def test_powerschool_diagnoses_archived_route_without_reactivating_it():
    with application.app.app_context():
        bus_id = _bus('TT', '88', active=False)
        bus = application.db.session.get(application.Bus, bus_id)
        assert application._powerschool_bus_for_route('TT', '88') is None
        diagnosed = application._powerschool_bus_for_route(
            'TT', '88', active_only=False)
        assert diagnosed.id == bus_id
        assert diagnosed.lifecycle_state == 'inactive'
        assert bus.active is False


def test_inventory_query_budget_is_bounded_for_full_fleet(logged_in_client):
    with application.app.app_context():
        application.db.session.add_all([
            application.Bus(identifier='TT', name=f'{number:03d}', active=True)
            for number in range(1, 114)
        ])
        application.db.session.commit()
        engine = application.db.engine
    statements = []

    def count_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, 'before_cursor_execute', count_statement)
    try:
        response = logged_in_client.get('/admin/buses')
    finally:
        event.remove(engine, 'before_cursor_execute', count_statement)
    assert response.status_code == 200
    assert len(statements) <= 40
    assert len(response.data) < 500_000


def test_inventory_javascript_normalizes_combined_bus_identity_and_persists_view():
    javascript = (Path(__file__).resolve().parents[1] / 'static/js/admin_buses.js').read_text(
        encoding='utf-8')
    assert "replace(/[^a-z0-9]+/g, ' ')" in javascript
    assert "replace(/\\s+/g, '')" in javascript
    assert "localStorage.setItem(viewStorageKey, viewMode)" in javascript
    assert "'X-CSRF-Token': config.csrfToken" in javascript
    assert "fetch('/admin/buses/bulk-lifecycle'" in javascript
    assert 'Permanent deletion is intentionally unavailable in bulk.' in (
        Path(__file__).resolve().parents[1] / 'templates/admin/buses.html'
    ).read_text(encoding='utf-8')
    assert "stateMode === 'attention'" in javascript
    assert "stateMode === 'pending'" in javascript
