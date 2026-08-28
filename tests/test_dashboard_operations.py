import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import event

import app as application


def _incident_token(response):
    match = re.search(
        r'name="request_token" value="([0-9a-f]{48})"',
        response.get_data(as_text=True),
    )
    assert match
    return match.group(1)


def _login(client, username, password):
    client.get('/admin/login')
    with client.session_transaction() as session:
        token = session['_csrf']
    response = client.post('/admin/login', data={
        '_csrf': token, 'username': username, 'password': password,
    })
    assert response.status_code == 302


def test_dashboard_snapshot_uses_unique_bus_identity_and_closed_date_range(
        logged_in_client, monkeypatch):
    with application.app.app_context():
        today = application.district_today()
        delayed = application.IncidentType.query.filter_by(name='Delayed').one()
        first = application.Bus(identifier='TT', name='01', active=True)
        second = application.Bus(identifier='TT', name='02', active=True)
        application.db.session.add_all([first, second])
        application.db.session.flush()
        application.db.session.add_all([
            application.BusIncidentRecord(
                bus_id=first.id, incident_type_id=delayed.id,
                incident_date=today, delay_minutes=10, is_pending=False),
            application.BusIncidentRecord(
                bus_id=second.id, incident_type_id=delayed.id,
                incident_date=today, delay_minutes=20, is_pending=False),
            application.BusIncidentRecord(
                bus_id=second.id, incident_type_id=delayed.id,
                incident_date=today + timedelta(days=1), is_pending=False),
        ])
        application.db.session.commit()
        monkeypatch.setattr(application, 'get_current_period', lambda cfg=None: None)
        snapshot = application._build_dashboard_snapshot(
            'custom', today.isoformat(), today.isoformat(),
            can_view_buses=True, can_view_statistics=True,
            can_view_notifications=False,
        )
        assert snapshot['by_bus'] == {'TT - 02': 1, 'TT - 01': 1}
        assert snapshot['period_incidents'] == 2
        assert {record.incident_date for record in snapshot['recent']} == {today}
        assert [bus['name'] for bus in snapshot['attention_buses']] == ['02', '01']


def test_dashboard_query_budget_is_bounded_for_full_fleet(logged_in_client):
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
        response = logged_in_client.get('/admin/dashboard')
    finally:
        event.remove(engine, 'before_cursor_execute', count_statement)
    assert response.status_code == 200
    assert len(statements) <= 35
    assert len(response.data) < 350_000


def test_dashboard_widgets_follow_module_permissions(client):
    with application.app.app_context():
        group = application.UserGroup(
            name='Dashboard Restricted', is_admin=False)
        application.db.session.add(group)
        application.db.session.flush()
        for module in application.MODULES:
            application.db.session.add(application.GroupPermission(
                group_id=group.id, module_key=module['key'], access_level='none'))
        user = application.User(
            username='restricted-dashboard', group_id=group.id, active=True)
        user.set_password('Restricted-Dashboard-Password')
        application.db.session.add(user)
        application.db.session.add(application.Bus(
            identifier='PRIVATE', name='BUS', active=True))
        application.db.session.commit()
    _login(client, 'restricted-dashboard', 'Restricted-Dashboard-Password')
    response = client.get('/admin/dashboard')
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'No operational modules assigned' in body
    assert 'PRIVATE - BUS' not in body
    assert 'Live bus operations' not in body
    assert 'Historical summary' not in body


def test_incident_submission_is_validated_audited_and_idempotent(
        logged_in_client):
    with application.app.app_context():
        bus = application.Bus(identifier='TR', name='10', active=True)
        application.db.session.add(bus)
        application.db.session.commit()
        bus_id = bus.id
        delayed_id = application.IncidentType.query.filter_by(name='Delayed').one().id

    dashboard = logged_in_client.get('/admin/dashboard')
    request_token = _incident_token(dashboard)
    with logged_in_client.session_transaction() as session:
        csrf = session['_csrf']
    payload = {
        '_csrf': csrf,
        'request_token': request_token,
        'next': '/admin/dashboard',
        'incident_type_id': delayed_id,
        'delay_minutes': 12,
        'eta': '15:45',
        'delay_reason_text': 'Traffic',
        'notes': 'Operational test',
    }
    first = logged_in_client.post(f'/admin/buses/{bus_id}/incident', data=payload)
    duplicate = logged_in_client.post(f'/admin/buses/{bus_id}/incident', data=payload)
    assert first.status_code == 302
    assert duplicate.status_code == 302
    with application.app.app_context():
        records = application.BusIncidentRecord.query.filter_by(bus_id=bus_id).all()
        assert len(records) == 1
        assert records[0].request_token == request_token
        assert application.AuditLog.query.filter_by(
            action='add_bus_incident', target='TR — 10').count() == 1

    fresh_token = _incident_token(logged_in_client.get('/admin/dashboard'))
    invalid = logged_in_client.post(f'/admin/buses/{bus_id}/incident', data={
        **payload, 'request_token': fresh_token, 'eta': '99:99',
    })
    assert invalid.status_code == 302
    with application.app.app_context():
        assert application.BusIncidentRecord.query.filter_by(bus_id=bus_id).count() == 1


def test_dashboard_template_is_exception_first_and_reuses_incident_form():
    root = Path(__file__).resolve().parents[1]
    dashboard = (root / 'templates/admin/dashboard.html').read_text(encoding='utf-8')
    buses = (root / 'templates/admin/buses.html').read_text(encoding='utf-8')
    javascript = (root / 'static/js/admin_dashboard.js').read_text(encoding='utf-8')
    assert dashboard.index('Needs attention') < dashboard.index('On-time buses')
    assert 'id="dashboard-bus-drawer"' in dashboard
    assert 'id="on-time-bus-section"' in dashboard
    assert 'Most affected buses' in dashboard
    assert "include 'admin/_incident_form_fields.html'" in dashboard
    assert "include 'admin/_incident_form_fields.html'" in buses
    assert 'data-summary-filter="attention"' in dashboard
    assert 'event.key === \'Escape\'' in javascript
    assert 'request_token' in (root / 'templates/admin/_incident_form_fields.html').read_text(
        encoding='utf-8')


def test_admin_header_uses_district_timezone():
    root = Path(__file__).resolve().parents[1]
    base = (root / 'templates/admin/base.html').read_text(encoding='utf-8')
    assert 'name="district-timezone"' in base
    assert 'timeZone:districtTimeZone' in base
