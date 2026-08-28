import json
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

import app as application


def _csrf(client):
    client.get('/admin/buses')
    with client.session_transaction() as session:
        return session['_csrf']


def _add_bus(identifier='TT', name='01'):
    bus = application.Bus(identifier=identifier, name=name, route='North Route')
    application.db.session.add(bus)
    application.db.session.commit()
    return bus


def test_district_today_uses_configured_timezone_at_utc_date_boundary():
    with application.app.app_context():
        cfg = application.get_config()
        cfg.timezone = 'America/Chicago'
        utc_instant = datetime(2026, 8, 28, 1, 15, tzinfo=timezone.utc)
        assert application.district_now(cfg, utc_instant).isoformat().startswith(
            '2026-08-27T20:15:00')
        assert application.district_today(cfg, utc_instant) == date(2026, 8, 27)


def test_public_api_has_stable_revision_etag_and_rendered_cards(client, monkeypatch):
    monkeypatch.setattr(application, 'get_current_period', lambda: None)
    with application.app.app_context():
        bus = _add_bus()
        delayed = application.IncidentType.query.filter_by(name='Delayed').one()
        application.db.session.add(application.BusIncidentRecord(
            bus_id=bus.id,
            incident_type_id=delayed.id,
            delay_minutes=12,
            incident_date=application.district_today(),
            delay_reason_text='Traffic',
        ))
        application.db.session.commit()

    response = client.get('/api/buses?render=1')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['operational'] is True
    assert payload['attention_count'] == 1
    assert len(payload['revision']) == 64
    assert payload['buses'][0]['status']['name'] == 'Delayed'
    assert payload['buses'][0]['status']['is_default'] is False
    assert 'data-identifier="TT"' in payload['cards_html']
    assert 'aria-pressed="false"' in payload['cards_html']
    etag = response.headers['ETag']

    unchanged = client.get('/api/buses?render=1', headers={'If-None-Match': etag})
    assert unchanged.status_code == 304
    assert unchanged.headers['ETag'] == etag
    assert unchanged.data == b''


def test_public_page_exposes_accessible_live_filter_contract(client, monkeypatch):
    monkeypatch.setattr(application, 'get_current_period', lambda: None)
    with application.app.app_context():
        _add_bus()
    response = client.get('/')
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '<label class="sr-only" for="filter-search">' in body
    assert '<label class="sr-only" for="filter-status">' in body
    assert 'id="attention-summary"' in body
    assert 'id="result-count"' in body and 'aria-live="polite"' in body
    assert '/static/js/public_portal.js' in body
    assert 'id="mobile-bottom-nav"' in body
    assert 'id="filter-sheet"' in body
    assert 'data-nav-action="alerts"' in body
    assert '/manifest.webmanifest' in body
    assert '/static/css/public.css' in body
    assert 'cdn.tailwindcss.com' not in body
    assert 'cdnjs.cloudflare.com' not in body
    config_text = body.split(
        '<script id="public-portal-config" type="application/json">', 1
    )[1].split('</script>', 1)[0]
    config = json.loads(config_text)
    assert config['timeZone'] == 'America/Chicago'
    assert config['pollIntervalMs'] == 30000


def test_public_page_translates_fixed_statuses_and_controls_to_spanish(client, monkeypatch):
    monkeypatch.setattr(application, 'get_current_period', lambda: None)
    with application.app.app_context():
        cfg = application.get_config()
        cfg.lang_frontend = 'es'
        bus = _add_bus()
        delayed = application.IncidentType.query.filter_by(name='Delayed').one()
        application.db.session.add(application.BusIncidentRecord(
            bus_id=bus.id,
            incident_type_id=delayed.id,
            incident_date=application.district_today(),
        ))
        application.db.session.commit()
    body = client.get('/').get_data(as_text=True)
    assert 'Atención de servicio' in body
    assert 'Retrasado' in body
    assert 'Buscar buses' in body
    assert 'Actualizaciones de hoy' in body
    assert '>Inicio<' in body
    assert '>Alertas<' in body
    assert 'Filtros y leyenda' in body


def test_pwa_manifest_service_worker_and_offline_contract(client):
    manifest_response = client.get('/manifest.webmanifest')
    assert manifest_response.status_code == 200
    assert manifest_response.mimetype == 'application/manifest+json'
    manifest = manifest_response.get_json()
    assert manifest['start_url'] == '/'
    assert manifest['scope'] == '/'
    assert manifest['display'] == 'standalone'
    assert {icon['sizes'] for icon in manifest['icons']} == {'192x192', '512x512'}

    worker_response = client.get('/service-worker.js')
    worker = worker_response.get_data(as_text=True)
    assert worker_response.status_code == 200
    assert worker_response.headers['Service-Worker-Allowed'] == '/'
    assert worker_response.headers['Cache-Control'] == 'no-cache, max-age=0'
    assert "url.pathname.startsWith('/api/')" in worker
    assert 'caches.match(OFFLINE_URL)' in worker
    assert "'/static/css/public.css'" in worker

    offline_response = client.get('/offline')
    offline = offline_response.get_data(as_text=True)
    assert offline_response.status_code == 200
    assert 'Current bus statuses require an internet connection.' in offline
    assert 'bus-card' not in offline


def test_local_public_assets_and_pwa_icons_are_built():
    root = Path(__file__).resolve().parents[1]
    css = root / 'static' / 'css' / 'public.css'
    fontawesome = root / 'static' / 'vendor' / 'fontawesome' / 'css' / 'all.min.css'
    solid_font = root / 'static' / 'vendor' / 'fontawesome' / 'webfonts' / 'fa-solid-900.woff2'
    assert css.stat().st_size > 10_000
    assert fontawesome.stat().st_size > 10_000
    assert solid_font.stat().st_size > 10_000
    for size in (192, 512):
        with Image.open(root / 'static' / 'icons' / f'bus-route-{size}.png') as icon:
            assert icon.size == (size, size)
            assert icon.format == 'PNG'


def test_schedule_departure_time_is_rejected_outside_period_window(logged_in_client):
    with application.app.app_context():
        afternoon = application.BusScheduleType.query.filter_by(name='Afternoon').one()
        afternoon_id = afternoon.id

    response = logged_in_client.post('/admin/buses/add', data={
        '_csrf': _csrf(logged_in_client),
        'identifier': 'AFL',
        'name': '02',
        'schedule_ids': str(afternoon_id),
        f'departure_time_{afternoon_id}': '02:40',
    })
    assert response.status_code == 302
    with application.app.app_context():
        assert application.Bus.query.filter_by(identifier='AFL', name='02').first() is None

    response = logged_in_client.post('/admin/buses/add', data={
        '_csrf': _csrf(logged_in_client),
        'identifier': 'AFL',
        'name': '02',
        'schedule_ids': str(afternoon_id),
        f'departure_time_{afternoon_id}': '14:40',
    })
    assert response.status_code == 302
    with application.app.app_context():
        bus = application.Bus.query.filter_by(identifier='AFL', name='02').one()
        assert bus.schedule_assignments[0].departure_time == '14:40'


def test_existing_invalid_schedule_time_gets_non_destructive_warning():
    with application.app.app_context():
        afternoon = application.BusScheduleType.query.filter_by(name='Afternoon').one()
        assignment = application.BusScheduleAssignment(
            departure_time='02:40', schedule_type=afternoon)
        warning = application.schedule_assignment_warning(assignment)
        assert 'outside the Afternoon window' in warning
        assignment.departure_time = '14:40'
        assert application.schedule_assignment_warning(assignment) is None


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js is not installed')
def test_public_portal_javascript_contract():
    test_file = Path(__file__).parent / 'js' / 'public_portal.test.cjs'
    result = subprocess.run(
        ['node', '--test', str(test_file)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
