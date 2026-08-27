import csv
import hashlib
import io
import json
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest

import app as application
from powerschool_import import (
    DEFAULT_MAPPING_V1,
    ImportValidationError,
    build_normalized_plan,
)
from conftest import _database_url_for_tests, csrf_token, login
from test_phase1_security import add_group, add_user


TRANSPORT_HEADER = (
    'student_number,student_id,household_id,first_name,last_name,school,grade,'
    'route,stop,period,transport_status,school_year,source_id\n'
)
TRANSPORT_V2_HEADER = (
    'student_number,first_name,last_name,grade,transport_status,route_am,'
    'route_pm,school,student_id\n'
)
CONTACT_HEADER = (
    'student_number,contact_id,first_name,last_name,relationship,email,phone,'
    'notification_preference,priority\n'
)
POWERSCHOOL_TRANSPORT_HEADER = ','.join([
    'TRANSPORTATION.student_number', 'student_dcid', 'studentfname',
    'studentlname', 'schoolid', 'grade_level', 'busnumber', 'stopnumber',
    'fromto', 'ride_on_enabledToday',
]) + '\n'
POWERSCHOOL_CONTACT_HEADER = ','.join([
    'BRIGHTARROW.003_student_number', '600_00_contact_id',
    '600_04_contact_std_detailid', '600_01_contact_firstname',
    '600_02_contact_lastname', '600_03_contact_relationship',
    '601_01_home_phone', '602_01_phone2', '603_01_phone3', '604_01_phone4',
    '605_01_phone5', '606_01_phone6', '607_01_phone7', '608_01_phone8',
    '609_01_phone9', '801_email1', '802_email2', '803_email3',
]) + '\n'


@pytest.fixture(autouse=True)
def enable_powerschool():
    previous = application.app.config['POWERSCHOOL_IMPORT_ENABLED']
    application.app.config['POWERSCHOOL_IMPORT_ENABLED'] = True
    yield
    application.app.config['POWERSCHOOL_IMPORT_ENABLED'] = previous


def setup_route():
    with application.app.app_context():
        morning = application.BusScheduleType.query.filter_by(name='Morning').one()
        afternoon = application.BusScheduleType.query.filter_by(name='Afternoon').one()
        bus = application.Bus(
            identifier='TEST', name='1', route='Test Route', active=True)
        application.db.session.add(bus)
        application.db.session.flush()
        application.db.session.add_all([
            application.BusScheduleAssignment(
                bus_id=bus.id, schedule_type_id=morning.id),
            application.BusScheduleAssignment(
                bus_id=bus.id, schedule_type_id=afternoon.id),
        ])
        application.db.session.commit()


def transport_row(student='0001', first='Ada', last='Lovelace', period='AM',
                  school='205'):
    return f'{student},SID-{student},HH-{student},{first},{last},{school},5,TEST1,10,{period},active,2026-27,T-{student}-{period}\n'


def transport_v2_row(student='0001', first='Ada', last='Lovelace',
                     route_am='TEST1 AM', route_pm='TEST1 PM', school='205'):
    return (f'{student},{first},{last},5,active,{route_am},{route_pm},'
            f'{school},SID-{student}\n')


def contact_row(student='0001', contact='C-1', first='Grace', last='Hopper',
                relationship='guardian', email='GRACE@EXAMPLE.TEST',
                phone='708-555-0101'):
    return f'{student},{contact},{first},{last},{relationship},{email},{phone},both,1\n'


def profile_id():
    with application.app.app_context():
        return application.ImportMappingProfile.query.filter_by(
            source_type='powerschool', schema_version='1').one().id


def seed_applied_legacy_subscriber(notes='Legacy roster subscriber'):
    with application.app.app_context():
        owner = application.User.query.filter_by(username='admin').one()
        subscriber = application.NotificationSubscriber(
            notes=notes, active=True)
        application.db.session.add(subscriber)
        application.db.session.flush()
        now = max(application._utcnow(), subscriber.created_at)
        batch = application.ImportBatch(
            public_id=application.secrets.token_urlsafe(24),
            source_type='legacy_csv', schema_version='1', status='applied',
            snapshot_type='delta', school_year='2026-27',
            uploaded_by_id=owner.id, file_sha256='a' * 64,
            plan_hash='b' * 64, total_rows=1, selected_rows=1,
            rejected_rows=0, excluded_rows=0, metadata_json='{}',
            created_at=now, applied_at=now,
            expires_at=now + timedelta(days=1))
        application.db.session.add(batch)
        application.db.session.flush()
        row = application.ImportRow(
            batch_id=batch.id, row_number=1, external_key=None,
            classification='new', selected=True, normalized_json='{}',
            errors_json='[]', row_hash='c' * 64)
        application.db.session.add(row)
        application.db.session.flush()
        application.db.session.add(application.ImportChange(
            batch_id=batch.id, row_id=row.id, operation='create',
            target_table='notification_subscriber', target_id=subscriber.id,
            after_json=json.dumps({'subscriber_id': subscriber.id})))
        application.db.session.commit()
        return subscriber.id


def seed_manual_preservation(subscriber_id):
    with application.app.app_context():
        owner = application.User.query.filter_by(username='admin').one()
        subscriber = application.db.session.get(
            application.NotificationSubscriber, subscriber_id)
        application._record_manual_subscriber_provenance(subscriber, owner)
        application.db.session.commit()


def preview(client, transportation, contacts, snapshot='delta'):
    return client.post('/admin/notifications/powerschool/preview', data={
        '_csrf': csrf_token(client), 'school_year': '2026-27',
        'snapshot_type': snapshot, 'mapping_profile_id': str(profile_id()),
        'transportation_file': (
            io.BytesIO((TRANSPORT_HEADER + transportation).encode()),
            'transportation.csv', 'text/csv'),
        'contacts_file': (
            io.BytesIO((CONTACT_HEADER + contacts).encode()),
            'contacts.csv', 'text/csv'),
    }, content_type='multipart/form-data')


def preview_v2(client, transportation, contacts, snapshot='delta'):
    return client.post('/admin/notifications/powerschool/preview', data={
        '_csrf': csrf_token(client), 'school_year': '2026-27',
        'snapshot_type': snapshot, 'mapping_profile_id': str(profile_id()),
        'transportation_file': (
            io.BytesIO((TRANSPORT_V2_HEADER + transportation).encode()),
            'transportation-v2.csv', 'text/csv'),
        'contacts_file': (
            io.BytesIO((CONTACT_HEADER + contacts).encode()),
            'contacts.csv', 'text/csv'),
    }, content_type='multipart/form-data')


def preview_split(client, transportation, student_contacts, guardian_contacts,
                  snapshot='delta'):
    return client.post('/admin/notifications/powerschool/preview', data={
        '_csrf': csrf_token(client), 'school_year': '2026-27',
        'snapshot_type': snapshot, 'mapping_profile_id': str(profile_id()),
        'transportation_file': (
            io.BytesIO((TRANSPORT_HEADER + transportation).encode()),
            'transportation.csv', 'text/csv'),
        'student_contacts_file': (
            io.BytesIO((CONTACT_HEADER + student_contacts).encode()),
            'student-contacts.csv', 'text/csv'),
        'guardian_contacts_file': (
            io.BytesIO((CONTACT_HEADER + guardian_contacts).encode()),
            'guardian-contacts.csv', 'text/csv'),
    }, content_type='multipart/form-data')


def test_split_powerschool_contacts_are_combined_with_safe_roles():
    parsed = build_normalized_plan(
        (TRANSPORT_HEADER + transport_row()).encode(), None,
        DEFAULT_MAPPING_V1, 10, 50,
        contact_sources=[
            {
                'key': 'student_contacts',
                'payload': (CONTACT_HEADER + contact_row(
                    contact='STUDENT-1', relationship='guardian',
                    email='student@example.test')).encode(),
                'force_relationship': 'student',
            },
            {
                'key': 'guardian_contacts',
                'payload': (CONTACT_HEADER + contact_row(
                    contact='GUARDIAN-1', relationship='',
                    email='guardian@example.test')).encode(),
                'default_relationship': 'guardian',
            },
        ])
    contacts = {item['contact_id']: item for item in parsed['students'][0]['contacts']}
    assert contacts['STUDENT-1']['relationship'] == 'student'
    assert contacts['GUARDIAN-1']['relationship'] == 'guardian'
    assert set(parsed['files']) == {
        'transportation', 'student_contacts', 'guardian_contacts'}


def test_exact_saved_template_headers_resolve_without_manual_changes():
    transport = POWERSCHOOL_TRANSPORT_HEADER + ','.join([
        '0001', 'SID-0001', 'Ada', 'Lovelace', '205', '5', 'TEST1', '10',
        'AM', 'true',
    ]) + '\n'
    student = POWERSCHOOL_CONTACT_HEADER + ','.join([
        '0001', 'STUDENT-1', '', 'Ada', 'Lovelace', '', '708-555-0101',
        '', '', '', '', '', '', '', '', 'student@example.test', '', '',
    ]) + '\n'
    guardian = POWERSCHOOL_CONTACT_HEADER + ','.join([
        '0001', 'GUARDIAN-1', '', 'Grace', 'Hopper', 'Mother', '708-555-0102',
        '', '', '', '', '', '', '', '', 'guardian@example.test', '', '',
    ]) + '\n'
    parsed = build_normalized_plan(
        transport.encode(), None, DEFAULT_MAPPING_V1, 10, 50,
        contact_sources=[
            {'key': 'student_contacts', 'payload': student.encode(),
             'force_relationship': 'student'},
            {'key': 'guardian_contacts', 'payload': guardian.encode(),
             'default_relationship': 'guardian'},
        ])
    proposal = parsed['students'][0]
    assert proposal['student_number'] == '0001'
    assert proposal['student_id'] == 'SID-0001'
    assert proposal['school'] == '205'
    assert proposal['grade'] == '5'
    assert proposal['assignments'][0] == {
        'route_prefix': 'TEST', 'route_number': '1', 'period': 'AM'}
    assert {item['email'] for item in proposal['contacts']} == {
        'student@example.test', 'guardian@example.test'}


def test_managed_header_aliases_merge_without_overwriting_custom_profile():
    with application.app.app_context():
        profile = application.ImportMappingProfile.query.filter_by(
            source_type='powerschool', schema_version='1').one()
        mapping = json.loads(profile.mapping_json)
        mapping['files']['transportation']['columns']['route'] = ['custom_route']
        profile.mapping_json = json.dumps(mapping)
        application.db.session.commit()
        application._seed_phase2_security_and_imports()
        merged = json.loads(application.ImportMappingProfile.query.filter_by(
            source_type='powerschool', schema_version='1').one().mapping_json)
        route_aliases = merged['files']['transportation']['columns']['route']
        assert 'custom_route' in route_aliases
        assert 'busnumber' in route_aliases
        assert 'TRANSPORTATION.busnumber' in route_aliases
        assert merged['files']['transportation']['columns']['route_am'] == [
            'route_am', 'BRIGHTARROW.013_bus_route']
        assert merged['files']['transportation']['columns']['route_pm'] == [
            'route_pm', 'BRIGHTARROW.014_bus_stop']


def test_split_contact_sources_share_one_cumulative_row_limit():
    with pytest.raises(ImportValidationError, match='more than 1 total data rows'):
        build_normalized_plan(
            (TRANSPORT_HEADER + transport_row()).encode(), None,
            DEFAULT_MAPPING_V1, 1, 50,
            contact_sources=[
                {'key': 'student_contacts',
                 'payload': (CONTACT_HEADER + contact_row(
                     contact='STUDENT-1')).encode(),
                 'force_relationship': 'student'},
                {'key': 'guardian_contacts',
                 'payload': (CONTACT_HEADER + contact_row(
                     contact='GUARDIAN-1')).encode(),
                 'default_relationship': 'guardian'},
            ])


def apply_batch(client, report):
    return client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/apply",
        json={'plan_hash': report['plan_hash']},
        headers={'X-CSRF-Token': csrf_token(client)})


def test_powerschool_new_apply_is_idempotent_reported_and_rollbackable(logged_in_client):
    setup_route()
    response = preview(
        logged_in_client,
        transport_row(period='AM') + transport_row(period='PM'),
        contact_row())
    assert response.status_code == 200, response.get_data(as_text=True)
    report = response.get_json()
    assert report['counts']['new'] == 1
    assert report['selected'] == 1
    assert report['selected'] + report['excluded'] + report['rejected'] == report['total']
    row = next(item for item in report['rows'] if item['classification'] == 'new')
    assert row['data']['student_number'] == '0001'
    assert row['data']['group_name'] == 'TEST1 AM PM'
    assert row['data']['contacts'][0]['email'] == 'grace@example.test'
    assert row['data']['contacts'][0]['phone'] == '+17085550101'

    committed = apply_batch(logged_in_client, report)
    assert committed.status_code == 200, committed.get_data(as_text=True)
    with application.app.app_context():
        subscriber = application.NotificationSubscriber.query.one()
        assert subscriber.active and subscriber.group.name == 'TEST1 AM PM'
        assert {item.role for item in subscriber.contacts} == {'student', 'parent'}
        assert application.ExternalIdentity.query.filter_by(
            entity_type='student', external_key='0001',
            local_id=subscriber.id).one()
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        assert batch.status == 'applied'
        assert application.ImportFile.query.filter_by(batch_id=batch.id).count() == 0

    repeated = apply_batch(logged_in_client, report)
    assert repeated.status_code == 200
    assert repeated.get_json()['already_applied'] is True
    with application.app.app_context():
        assert application.NotificationSubscriber.query.count() == 1

    csv_report = logged_in_client.get(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/report.csv")
    assert csv_report.status_code == 200
    assert 'PowerSchool Import v1' in csv_report.get_data(as_text=True)

    rolled_back = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert rolled_back.status_code == 200, rolled_back.get_data(as_text=True)
    with application.app.app_context():
        assert application.NotificationSubscriber.query.count() == 0
        assert application.ExternalIdentity.query.count() == 0
        assert application.SubscriberGroup.query.count() == 0
        assert application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one().status == 'rolled_back'


def test_first_powerschool_batch_requires_atomic_legacy_cutover(logged_in_client):
    setup_route()
    legacy_id = seed_applied_legacy_subscriber()
    with application.app.app_context():
        manual = application.NotificationSubscriber(
            notes='Manual subscriber', active=True)
        application.db.session.add(manual)
        application.db.session.commit()
        manual_id = manual.id
    seed_manual_preservation(manual_id)

    report = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(),
        snapshot='full_district').get_json()
    cutover = report['legacy_cutover']
    assert cutover == {
        'required': True,
        'candidate_count': 1,
        'incarnation_excluded_count': 0,
        'baseline_required': False,
        'baseline_available': False,
        'unmanaged_count': 0,
        'approved': False,
        'blocked': False,
        'requires_reanalysis': False,
        'message': (
            'Approve the atomic Legacy CSV to PowerSchool roster cutover '
            'before applying this first PowerSchool batch.'),
    }
    legacy_row = next(
        row for row in report['rows']
        if row['data'].get('cutover_source') == 'legacy_csv')
    new_row = next(
        row for row in report['rows'] if row['classification'] == 'new')
    assert legacy_row['classification'] == 'deactivate_candidate'
    assert legacy_row['selected'] is False
    assert apply_batch(logged_in_client, report).status_code == 409

    approved = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/selection",
        json={
            'plan_hash': report['plan_hash'],
            'selected_row_ids': [new_row['id']],
            'deactivation_row_ids': [],
            'confirm_deactivations': False,
            'legacy_cutover_approved': True,
        },
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert approved.status_code == 200, approved.get_data(as_text=True)
    summary = approved.get_json()
    assert summary['selected'] == 2
    assert summary['legacy_cutover']['approved'] is True
    assert summary['legacy_cutover']['approved_by_id']
    assert summary['legacy_cutover']['approved_at'].endswith('Z')
    assert summary['legacy_cutover']['approved_plan_hash'] == summary['plan_hash']
    report['plan_hash'] = summary['plan_hash']
    committed = apply_batch(logged_in_client, report)
    assert committed.status_code == 200, committed.get_data(as_text=True)
    with application.app.app_context():
        assert application.db.session.get(
            application.NotificationSubscriber, legacy_id).active is False
        assert application.db.session.get(
            application.NotificationSubscriber, manual_id).active is True
        imported = application.ExternalIdentity.query.filter_by(
            source_type='powerschool', entity_type='student',
            external_key='0001').one()
        assert application.db.session.get(
            application.NotificationSubscriber, imported.local_id).active is True
        approval_audit = application.AuditLog.query.filter_by(
            action='powerschool_import_selection').order_by(
                application.AuditLog.id.desc()).first()
        assert 'legacy_cutover=approved' in approval_audit.details
        assert f'plan_hash={summary["plan_hash"]}' in approval_audit.details

    csv_report = logged_in_client.get(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/report.csv")
    assert csv_report.status_code == 200
    csv_text = csv_report.get_data(as_text=True)
    assert 'legacy_cutover_approved_plan_hash' in csv_text.splitlines()[0]
    assert summary['plan_hash'] in csv_text

    rolled_back = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert rolled_back.status_code == 200, rolled_back.get_data(as_text=True)
    with application.app.app_context():
        assert application.db.session.get(
            application.NotificationSubscriber, legacy_id).active is True
        assert application.db.session.get(
            application.NotificationSubscriber, manual_id).active is True
        assert application.ExternalIdentity.query.filter_by(
            source_type='powerschool', entity_type='student',
            external_key='0001').count() == 0


@pytest.mark.parametrize('selected_count', [0, 1])
def test_legacy_cutover_rejects_empty_or_partial_new_roster_selection(
        logged_in_client, selected_count):
    setup_route()
    legacy_id = seed_applied_legacy_subscriber()
    report = preview_v2(
        logged_in_client,
        transport_v2_row('0001')
        + transport_v2_row('0002', 'Alan', 'Turing'),
        contact_row('0001', 'C-1') + contact_row('0002', 'C-2'),
        snapshot='full_district',
    ).get_json()
    new_rows = [
        row for row in report['rows'] if row['classification'] == 'new'
    ]
    assert len(new_rows) == 2

    denied = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/selection",
        json={
            'plan_hash': report['plan_hash'],
            'selected_row_ids': [
                row['id'] for row in new_rows[:selected_count]
            ],
            'deactivation_row_ids': [],
            'confirm_deactivations': False,
            'legacy_cutover_approved': True,
        },
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert denied.status_code == 409
    assert 'every importable New and Update row' in denied.get_json()['message']

    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        assert batch.status == 'staged'
        assert application.db.session.get(
            application.NotificationSubscriber, legacy_id).active is True


def test_legacy_cutover_preserves_manual_subscriber_that_reuses_deleted_id(
        logged_in_client):
    setup_route()
    genuine_legacy_id = seed_applied_legacy_subscriber('Genuine legacy')
    deleted_legacy_id = seed_applied_legacy_subscriber('Deleted legacy')
    with application.app.app_context():
        deleted = application.db.session.get(
            application.NotificationSubscriber, deleted_legacy_id)
        application.db.session.delete(deleted)
        application.db.session.commit()
        manual = application.NotificationSubscriber(
            notes='Manual reused ID', active=True,
            created_at=application._utcnow() + timedelta(seconds=1))
        application.db.session.add(manual)
        application.db.session.commit()
        assert manual.id == deleted_legacy_id
        manual_id = manual.id
    seed_manual_preservation(manual_id)

    report = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(),
        snapshot='full_district').get_json()
    cutover = report['legacy_cutover']
    assert cutover['required'] is True
    assert cutover['candidate_count'] == 1
    assert cutover['incarnation_excluded_count'] == 1
    new_row = next(
        row for row in report['rows'] if row['classification'] == 'new')
    approved = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/selection",
        json={
            'plan_hash': report['plan_hash'],
            'selected_row_ids': [new_row['id']],
            'deactivation_row_ids': [],
            'confirm_deactivations': False,
            'legacy_cutover_approved': True,
        },
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert approved.status_code == 200, approved.get_data(as_text=True)
    report['plan_hash'] = approved.get_json()['plan_hash']
    assert apply_batch(logged_in_client, report).status_code == 200

    with application.app.app_context():
        assert application.db.session.get(
            application.NotificationSubscriber,
            genuine_legacy_id).active is False
        manual = application.db.session.get(
            application.NotificationSubscriber, manual_id)
        assert manual.active is True
        assert manual.notes == 'Manual reused ID'


def test_staged_batch_before_cutover_guard_must_be_reanalyzed(logged_in_client):
    setup_route()
    report = preview(
        logged_in_client, transport_row(), contact_row()).get_json()
    assert report['legacy_cutover']['required'] is False
    seed_applied_legacy_subscriber()

    refreshed = logged_in_client.get(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}")
    assert refreshed.status_code == 200
    cutover = refreshed.get_json()['legacy_cutover']
    assert cutover['required'] is True
    assert cutover['blocked'] is True
    assert cutover['requires_reanalysis'] is True
    denied = apply_batch(logged_in_client, report)
    assert denied.status_code == 409
    assert 'Analyze the three files again' in denied.get_json()['message']


def test_delta_batch_cannot_replace_the_district_legacy_roster(
        logged_in_client):
    setup_route()
    legacy_id = seed_applied_legacy_subscriber()
    report = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(), snapshot='delta',
    ).get_json()
    cutover = report['legacy_cutover']
    assert cutover['required'] is True
    assert cutover['blocked'] is True
    assert cutover['requires_reanalysis'] is True
    assert 'district-wide Full Snapshot' in cutover['message']
    assert not any(
        row['data'].get('cutover_source') == 'legacy_csv'
        for row in report['rows'])
    assert apply_batch(logged_in_client, report).status_code == 409
    with application.app.app_context():
        assert application.db.session.get(
            application.NotificationSubscriber, legacy_id).active is True


def test_v1_transport_cannot_cut_over_legacy_even_when_marked_full_snapshot(
        logged_in_client):
    setup_route()
    legacy_id = seed_applied_legacy_subscriber()
    response = preview(
        logged_in_client, transport_row(), contact_row(),
        snapshot='full_district')
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['code'] == (
        'powerschool_full_snapshot_requires_transportation_v2')
    assert 'approved Transportation v2' in payload['message']
    with application.app.app_context():
        assert application.ImportBatch.query.filter_by(
            source_type='powerschool').count() == 0
        assert application.db.session.get(
            application.NotificationSubscriber, legacy_id).active is True


def test_preexisting_staged_v1_full_snapshot_cannot_apply_deactivations(
        logged_in_client, monkeypatch):
    setup_route()
    first = preview(
        logged_in_client, transport_row('0001'),
        contact_row('0001', 'C-1')).get_json()
    assert apply_batch(logged_in_client, first).status_code == 200

    # Simulate the analysis behavior of a deployment before Full Snapshot was
    # restricted to Transportation v2.  Apply must still reject the stored plan.
    monkeypatch.setattr(
        application, '_powerschool_preflight_failure',
        lambda parsed, snapshot_type='delta': None)
    old_response = preview(
        logged_in_client, transport_row('0002', 'Alan', 'Turing'),
        contact_row('0002', 'C-2'), snapshot='full_district')
    assert old_response.status_code == 200, old_response.get_data(as_text=True)
    old_plan = old_response.get_json()
    candidate = next(
        row for row in old_plan['rows']
        if row['classification'] == 'deactivate_candidate')
    new_row = next(
        row for row in old_plan['rows'] if row['classification'] == 'new')
    selected = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{old_plan['batch_id']}/selection",
        json={
            'plan_hash': old_plan['plan_hash'],
            'selected_row_ids': [new_row['id']],
            'deactivation_row_ids': [candidate['id']],
            'confirm_deactivations': True,
        },
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert selected.status_code == 200
    old_plan['plan_hash'] = selected.get_json()['plan_hash']

    denied = apply_batch(logged_in_client, old_plan)
    assert denied.status_code == 409
    assert 'not analyzed from the approved Transportation v2' in (
        denied.get_json()['message'])
    with application.app.app_context():
        existing = application.ExternalIdentity.query.filter_by(
            entity_type='student', external_key='0001').one()
        assert application.db.session.get(
            application.NotificationSubscriber, existing.local_id).active is True
        assert application.ExternalIdentity.query.filter_by(
            entity_type='student', external_key='0002').count() == 0


def test_full_snapshot_can_clean_up_residual_legacy_when_roster_is_unchanged(
        logged_in_client):
    setup_route()
    initial = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(), snapshot='delta',
    ).get_json()
    assert apply_batch(logged_in_client, initial).status_code == 200
    legacy_id = seed_applied_legacy_subscriber('Residual legacy subscriber')

    report = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(),
        snapshot='full_district').get_json()
    assert report['counts']['unchanged'] == 1
    assert report['counts']['new'] == 0
    assert report['counts']['update'] == 0
    assert report['legacy_cutover']['required'] is True
    assert report['legacy_cutover']['blocked'] is False

    approved = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/selection",
        json={
            'plan_hash': report['plan_hash'],
            'selected_row_ids': [],
            'deactivation_row_ids': [],
            'confirm_deactivations': False,
            'legacy_cutover_approved': True,
        },
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert approved.status_code == 200, approved.get_data(as_text=True)
    summary = approved.get_json()
    assert summary['selected'] == 1
    report['plan_hash'] = summary['plan_hash']
    assert apply_batch(logged_in_client, report).status_code == 200

    with application.app.app_context():
        assert application.db.session.get(
            application.NotificationSubscriber, legacy_id).active is False
        powerschool_identity = application.ExternalIdentity.query.filter_by(
            source_type='powerschool', entity_type='student',
            external_key='0001').one()
        assert application.db.session.get(
            application.NotificationSubscriber,
            powerschool_identity.local_id).active is True


def test_staged_legacy_apply_is_blocked_after_powerschool_becomes_authoritative(
        logged_in_client):
    setup_route()
    legacy_csv = (
        'schema_version,subscriber_id,household_label,group,active,role,'
        'first_name,last_name,email,phone\n'
        'Legacy CSV v1,,Legacy Household,TEST 1,yes,parent,Legacy,Parent,'
        'legacy@example.test,17085550109\n'
    ).encode()
    staged = logged_in_client.post(
        '/admin/notifications/import-csv/preview',
        data={
            '_csrf': csrf_token(logged_in_client),
            'csv_file': (io.BytesIO(legacy_csv), 'legacy.csv', 'text/csv'),
        },
        content_type='multipart/form-data')
    assert staged.status_code == 200, staged.get_data(as_text=True)
    staged_report = staged.get_json()

    powerschool = preview(
        logged_in_client, transport_row(), contact_row()).get_json()
    assert apply_batch(logged_in_client, powerschool).status_code == 200
    blocked = logged_in_client.post(
        '/admin/notifications/import-csv',
        data={
            '_csrf': csrf_token(logged_in_client),
            'batch_id': staged_report['batch_id'],
            'plan_hash': staged_report['plan_hash'],
        })
    assert blocked.status_code == 302
    with application.app.app_context():
        assert application.NotificationSubscriber.query.count() == 1
        assert application.ImportBatch.query.filter_by(
            public_id=staged_report['batch_id']).one().status == 'staged'

    rejected_preview = logged_in_client.post(
        '/admin/notifications/import-csv/preview',
        data={
            '_csrf': csrf_token(logged_in_client),
            'csv_file': (io.BytesIO(legacy_csv), 'legacy.csv', 'text/csv'),
        },
        content_type='multipart/form-data')
    assert rejected_preview.status_code == 409
    assert 'active PowerSchool roster is authoritative' in (
        rejected_preview.get_json()['message'])


def test_roster_mutex_blocks_powerschool_apply(logged_in_client, monkeypatch):
    setup_route()
    report = preview(
        logged_in_client, transport_row(), contact_row()).get_json()

    @application.contextmanager
    def unavailable_roster_lock():
        yield False

    monkeypatch.setattr(application, '_roster_import_lock',
                        unavailable_roster_lock)
    denied = apply_batch(logged_in_client, report)
    assert denied.status_code == 409
    assert 'Another roster import or rollback is in progress' in (
        denied.get_json()['message'])
    with application.app.app_context():
        assert application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one().status == 'staged'
        assert application.NotificationSubscriber.query.count() == 0


@pytest.mark.parametrize('operation', ['get', 'selection', 'apply', 'report'])
def test_expired_powerschool_stage_cannot_mutate_roster(
        logged_in_client, operation):
    setup_route()
    report = preview(
        logged_in_client, transport_row(), contact_row()).get_json()
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        batch.expires_at = application._utcnow() - timedelta(seconds=1)
        application.db.session.commit()

    if operation == 'get':
        response = logged_in_client.get(
            f"/admin/notifications/powerschool/batch/{report['batch_id']}")
    elif operation == 'selection':
        new_row = next(
            row for row in report['rows'] if row['classification'] == 'new')
        response = logged_in_client.post(
            f"/admin/notifications/powerschool/batch/{report['batch_id']}/selection",
            json={
                'plan_hash': report['plan_hash'],
                'selected_row_ids': [new_row['id']],
                'deactivation_row_ids': [],
                'confirm_deactivations': False,
            },
            headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    elif operation == 'apply':
        response = apply_batch(logged_in_client, report)
    else:
        response = logged_in_client.get(
            f"/admin/notifications/powerschool/batch/"
            f"{report['batch_id']}/report.csv")

    assert response.status_code == 410
    assert response.get_json()['status'] == 'expired'
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        assert batch.status == 'expired'
        assert application.ImportRow.query.filter_by(
            batch_id=batch.id).count() == 0
        assert application.ImportFile.query.filter_by(
            batch_id=batch.id).count() == 0
        assert application.NotificationSubscriber.query.count() == 0


def test_apply_rechecks_expiry_after_claim(logged_in_client, monkeypatch):
    setup_route()
    report = preview(
        logged_in_client, transport_row(), contact_row()).get_json()
    original = application._expire_powerschool_stage
    calls = {'count': 0}

    def expire_after_claim(batch):
        calls['count'] += 1
        if calls['count'] == 2:
            batch.expires_at = application._utcnow() - timedelta(seconds=1)
        return original(batch)

    monkeypatch.setattr(
        application, '_expire_powerschool_stage', expire_after_claim)
    denied = apply_batch(logged_in_client, report)
    assert denied.status_code == 410
    assert calls['count'] == 2
    with application.app.app_context():
        assert application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one().status == 'expired'
        assert application.NotificationSubscriber.query.count() == 0


def test_rollback_fails_closed_after_imported_subscriber_is_edited(
        logged_in_client):
    setup_route()
    report = preview(
        logged_in_client, transport_row(), contact_row()).get_json()
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        subscriber = application.NotificationSubscriber.query.one()
        subscriber.notes = 'Edited after import'
        application.db.session.commit()

    denied = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert denied.status_code == 409
    assert 'edited later' in denied.get_json()['message']
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        assert batch.status == 'applied'
        assert application.NotificationSubscriber.query.one().notes == (
            'Edited after import')


def test_rollback_fails_closed_after_subscriber_pk_is_reused_identically(
        logged_in_client):
    setup_route()
    report = preview(
        logged_in_client, transport_row(), contact_row()).get_json()
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        change = application.ImportChange.query.filter_by(
            target_table='notification_subscriber').one()
        change_created_at = change.created_at
        expected = json.loads(change.after_json)
        assert expected['created_at']
        subscriber = application.NotificationSubscriber.query.one()
        application.db.session.delete(subscriber)
        application.db.session.commit()

        replacement = application.NotificationSubscriber(
            id=expected['id'], notes=expected['notes'],
            active=expected['active'], group_id=expected['group_id'],
            created_at=change_created_at + timedelta(seconds=1))
        application.db.session.add(replacement)
        application.db.session.flush()
        for item in expected['contacts']:
            application.db.session.add(application.SubscriberContact(
                id=item['id'], subscriber_id=replacement.id,
                first_name=item['first_name'], last_name=item['last_name'],
                email=item['email'], phone=item['phone'], role=item['role'],
                sort_order=item['sort_order']))
        application.db.session.commit()
        current = application._subscriber_snapshot(replacement)
        current.pop('created_at')
        identical = dict(expected)
        identical.pop('created_at')
        assert current == identical
        assert replacement.created_at > change_created_at

    denied = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert denied.status_code == 409
    assert 'edited later' in denied.get_json()['message']
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        assert batch.status == 'applied'
        assert application.NotificationSubscriber.query.one().created_at > (
            change_created_at)


def test_rollback_fails_closed_after_imported_group_is_edited(
        logged_in_client):
    setup_route()
    report = preview(
        logged_in_client, transport_row(), contact_row()).get_json()
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        group = application.SubscriberGroup.query.one()
        group.description = 'Edited after import'
        application.db.session.commit()

    denied = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert denied.status_code == 409
    assert 'imported group was edited later' in denied.get_json()['message']
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        assert batch.status == 'applied'
        assert application.SubscriberGroup.query.one().description == (
            'Edited after import')
        assert application.NotificationSubscriber.query.count() == 1


def test_rollback_fails_closed_after_group_pk_is_reused_identically(
        logged_in_client):
    setup_route()
    report = preview(
        logged_in_client, transport_row(), contact_row()).get_json()
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        change = application.ImportChange.query.filter_by(
            target_table='subscriber_group', operation='create').one()
        change_created_at = change.created_at
        expected = json.loads(change.after_json)
        assert expected['created_at']
        subscriber = application.NotificationSubscriber.query.one()
        group = application.SubscriberGroup.query.one()
        subscriber.group_id = None
        application.db.session.delete(group)
        application.db.session.commit()

        replacement = application.SubscriberGroup(
            id=expected['id'], name=expected['name'],
            description=expected['description'], color=expected['color'],
            created_at=change_created_at + timedelta(seconds=1))
        application.db.session.add(replacement)
        application.db.session.flush()
        for bus_id, schedule_type_id in expected['assignments']:
            application.db.session.add(application.GroupBusAssignment(
                group_id=replacement.id, bus_id=bus_id,
                schedule_type_id=schedule_type_id))
        subscriber = application.NotificationSubscriber.query.one()
        subscriber.group_id = replacement.id
        application.db.session.commit()
        current = application._subscriber_group_snapshot(replacement)
        current.pop('created_at')
        identical = dict(expected)
        identical.pop('created_at')
        assert current == identical
        assert replacement.created_at > change_created_at

    denied = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert denied.status_code == 409
    assert 'imported group was edited later' in denied.get_json()['message']
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        assert batch.status == 'applied'
        assert application.SubscriberGroup.query.one().created_at > (
            change_created_at)
        assert application.NotificationSubscriber.query.count() == 1


def test_rollback_accepts_unchanged_old_snapshots_without_created_at(
        logged_in_client):
    setup_route()
    report = preview(
        logged_in_client, transport_row(), contact_row()).get_json()
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        changes = application.ImportChange.query.filter(
            application.ImportChange.target_table.in_([
                'notification_subscriber', 'subscriber_group',
            ])).all()
        assert {change.target_table for change in changes} == {
            'notification_subscriber', 'subscriber_group',
        }
        for change in changes:
            snapshot = json.loads(change.after_json)
            assert snapshot.pop('created_at')
            change.after_json = json.dumps(snapshot, sort_keys=True)
        application.db.session.commit()

    rolled_back = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert rolled_back.status_code == 200, rolled_back.get_data(as_text=True)
    with application.app.app_context():
        assert application.NotificationSubscriber.query.count() == 0
        assert application.SubscriberGroup.query.count() == 0


def test_rollback_old_group_snapshot_still_detects_description_edit(
        logged_in_client):
    setup_route()
    report = preview(
        logged_in_client, transport_row(), contact_row()).get_json()
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        group_change = application.ImportChange.query.filter_by(
            target_table='subscriber_group', operation='create').one()
        old_snapshot = json.loads(group_change.after_json)
        old_snapshot.pop('description')
        old_snapshot.pop('color')
        old_snapshot.pop('created_at')
        group_change.after_json = json.dumps(old_snapshot, sort_keys=True)
        application.SubscriberGroup.query.one().description = (
            'Edited after old-format snapshot')
        application.db.session.commit()

    denied = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert denied.status_code == 409
    assert 'imported group was edited later' in denied.get_json()['message']


def test_legacy_cutover_is_blocked_until_student_conflicts_are_resolved(
        logged_in_client):
    setup_route()
    seed_applied_legacy_subscriber()
    transportation = (
        transport_v2_row('0001')
        + transport_v2_row(
            '0002', 'Alan', 'Turing', route_am='ELL1 AM',
            route_pm='ELL1 PM')
    )
    contacts = contact_row('0001', 'C-1') + contact_row('0002', 'C-2')
    response = preview_v2(
        logged_in_client, transportation, contacts,
        snapshot='full_district')
    assert response.status_code == 200, response.get_data(as_text=True)
    report = response.get_json()
    assert report['counts']['new'] == 1
    assert report['counts']['conflict'] == 1
    assert report['legacy_cutover']['required'] is True
    assert report['legacy_cutover']['blocked'] is True
    assert report['legacy_cutover']['requires_reanalysis'] is True
    assert not any(
        row['data'].get('cutover_source') == 'legacy_csv'
        for row in report['rows'])
    denied = apply_batch(logged_in_client, report)
    assert denied.status_code == 409
    assert 'Resolve every conflict' in denied.get_json()['message']


def test_split_exports_apply_student_and_guardian_contacts(logged_in_client):
    setup_route()
    response = preview_split(
        logged_in_client, transport_row(),
        contact_row(contact='STUDENT-1', relationship='',
                    email='student@example.test'),
        contact_row(contact='GUARDIAN-1', relationship='',
                    email='guardian@example.test'))
    assert response.status_code == 200, response.get_data(as_text=True)
    report = response.get_json()
    assert report['counts']['new'] == 1
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        contacts = application.SubscriberContact.query.order_by(
            application.SubscriberContact.role).all()
        assert {(item.role, item.email) for item in contacts} == {
            ('student', 'student@example.test'),
            ('parent', 'guardian@example.test'),
        }
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        assert application.ImportFile.query.filter_by(batch_id=batch.id).count() == 0


def test_powerschool_update_rollback_restores_previous_values(logged_in_client):
    setup_route()
    first = preview(logged_in_client, transport_row(), contact_row())
    first_report = first.get_json()
    assert apply_batch(logged_in_client, first_report).status_code == 200
    changed = preview(
        logged_in_client, transport_row(),
        contact_row(email='new-address@example.test'))
    assert changed.status_code == 200
    changed_report = changed.get_json()
    assert changed_report['counts']['update'] == 1
    assert any(change['field'] == 'contact.email'
               for change in changed_report['rows'][0]['data']['changes'])
    assert apply_batch(logged_in_client, changed_report).status_code == 200
    with application.app.app_context():
        assert application.SubscriberContact.query.filter_by(
            role='parent').one().email == 'new-address@example.test'
    rollback = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{changed_report['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert rollback.status_code == 200
    with application.app.app_context():
        assert application.SubscriberContact.query.filter_by(
            role='parent').one().email == 'grace@example.test'
        assert application.NotificationSubscriber.query.count() == 1


def test_exact_files_are_detected_instead_of_reparsed(logged_in_client):
    setup_route()
    assert preview(logged_in_client, transport_row(), contact_row()).status_code == 200
    duplicate = preview(logged_in_client, transport_row(), contact_row())
    assert duplicate.status_code == 409
    assert duplicate.get_json()['existing_batch_id']


def test_selection_is_hash_bound_and_reconciles_counts(logged_in_client):
    setup_route()
    report = preview(
        logged_in_client,
        transport_row('0001') + transport_row('0002', 'Alan', 'Turing'),
        contact_row('0001', 'C-1') + contact_row('0002', 'C-2')).get_json()
    new_rows = [item for item in report['rows'] if item['classification'] == 'new']
    selection = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/selection",
        json={'plan_hash': report['plan_hash'],
              'selected_row_ids': [new_rows[0]['id']],
              'deactivation_row_ids': [], 'confirm_deactivations': False},
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert selection.status_code == 200
    summary = selection.get_json()
    assert summary['plan_hash'] != report['plan_hash']
    assert summary['selected'] + summary['excluded'] + summary['rejected'] == summary['total']
    stale = apply_batch(logged_in_client, report)
    assert stale.status_code == 409
    committed = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/apply",
        json={'plan_hash': summary['plan_hash']},
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert committed.status_code == 200
    with application.app.app_context():
        assert application.NotificationSubscriber.query.count() == 1


def test_mid_transaction_failure_changes_no_operational_records(logged_in_client, monkeypatch):
    setup_route()
    report = preview(
        logged_in_client,
        transport_row('0001') + transport_row('0002', 'Alan', 'Turing'),
        contact_row('0001', 'C-1') + contact_row('0002', 'C-2')).get_json()
    original = application._apply_powerschool_proposal
    calls = {'count': 0}

    def fail_second(*args, **kwargs):
        calls['count'] += 1
        if calls['count'] == 2:
            raise RuntimeError('synthetic transaction failure')
        return original(*args, **kwargs)

    monkeypatch.setattr(application, '_apply_powerschool_proposal', fail_second)
    failed = apply_batch(logged_in_client, report)
    assert failed.status_code == 409
    with application.app.app_context():
        assert application.NotificationSubscriber.query.count() == 0
        assert application.SubscriberGroup.query.count() == 0
        assert application.ExternalIdentity.query.count() == 0
        assert application.ImportChange.query.count() == 0
        assert application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one().status == 'failed'


def test_complete_snapshot_never_selects_deactivation_without_separate_approval(logged_in_client):
    setup_route()
    first = preview_v2(
        logged_in_client, transport_v2_row('0001'),
        contact_row('0001', 'C-1')).get_json()
    assert apply_batch(logged_in_client, first).status_code == 200

    second = preview_v2(
        logged_in_client, transport_v2_row('0002', 'Alan', 'Turing'),
        contact_row('0002', 'C-2'), snapshot='full_district').get_json()
    candidate = next(row for row in second['rows']
                     if row['classification'] == 'deactivate_candidate')
    new_row = next(row for row in second['rows'] if row['classification'] == 'new')
    assert candidate['selected'] is False
    denied = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{second['batch_id']}/selection",
        json={'plan_hash': second['plan_hash'],
              'selected_row_ids': [new_row['id']],
              'deactivation_row_ids': [candidate['id']],
              'confirm_deactivations': False},
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert denied.status_code == 409
    with application.app.app_context():
        assert application.NotificationSubscriber.query.one().active is True

    approved = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{second['batch_id']}/selection",
        json={'plan_hash': second['plan_hash'],
              'selected_row_ids': [new_row['id']],
              'deactivation_row_ids': [candidate['id']],
              'confirm_deactivations': True},
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert approved.status_code == 200
    second['plan_hash'] = approved.get_json()['plan_hash']
    assert apply_batch(logged_in_client, second).status_code == 200
    with application.app.app_context():
        old = application.ExternalIdentity.query.filter_by(
            entity_type='student', external_key='0001').one()
        assert application.db.session.get(
            application.NotificationSubscriber, old.local_id).active is False

    rollback = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{second['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert rollback.status_code == 200
    with application.app.app_context():
        old = application.ExternalIdentity.query.filter_by(
            entity_type='student', external_key='0001').one()
        assert application.db.session.get(
            application.NotificationSubscriber, old.local_id).active is True
        assert application.ExternalIdentity.query.filter_by(
            entity_type='student', external_key='0002').count() == 0


def test_incomplete_snapshot_cannot_propose_deactivations(logged_in_client):
    setup_route()
    first = preview_v2(
        logged_in_client, transport_v2_row('0001'),
        contact_row('0001', 'C-1')).get_json()
    assert apply_batch(logged_in_client, first).status_code == 200
    incomplete_transport = (
        transport_v2_row('0002', 'Alan', 'Turing')
        + transport_v2_row(
            '0003', 'Bad', 'Route', route_am='ELL1 AM',
            route_pm='ELL1 PM')
    )
    response = preview_v2(
        logged_in_client, incomplete_transport,
        contact_row('0002', 'C-2'), snapshot='full_district')
    assert response.status_code == 200, response.get_data(as_text=True)
    report = response.get_json()
    assert report['counts']['conflict'] >= 1
    assert not any(row['classification'] == 'deactivate_candidate'
                   for row in report['rows'])
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        assert json.loads(batch.metadata_json)[
            'snapshot_complete_for_deactivation'] is False


def test_powerschool_capability_is_explicit_and_feature_flag_fail_closed(client):
    with application.app.app_context():
        group = add_group('PowerSchool Operators', {'notifications': 'full'})
        application._sync_group_capabilities(group.id)
        add_user('ps-operator', group)
        application.db.session.commit()
    login(client, 'ps-operator', 'Another-Safe-Password')
    assert client.get('/admin/notifications/powerschool').status_code == 403
    with application.app.app_context():
        group = application.UserGroup.query.filter_by(
            name='PowerSchool Operators').one()
        application.db.session.add(application.GroupCapability(
            group_id=group.id, capability_key='import.powerschool', granted=True))
        application.db.session.commit()
    assert client.get('/admin/notifications/powerschool').status_code == 200
    application.app.config['POWERSCHOOL_IMPORT_ENABLED'] = False
    assert client.get('/admin/notifications/powerschool').status_code == 404


def test_annual_guide_remains_available_when_importer_is_disabled(logged_in_client):
    application.app.config['POWERSCHOOL_IMPORT_ENABLED'] = False
    response = logged_in_client.get('/admin/notifications/powerschool-guide')
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'D205 BusRoute - Transportation v1' in page
    assert 'D205 BusRoute - Student Contacts v1' in page
    assert 'D205 BusRoute - Guardian Contacts v1' in page
    assert 'Importer disabled or not assigned to this account' in page


def test_preview_rejects_partial_or_mixed_contact_file_sets(logged_in_client):
    base = {
        '_csrf': csrf_token(logged_in_client), 'school_year': '2026-27',
        'snapshot_type': 'delta', 'mapping_profile_id': str(profile_id()),
        'transportation_file': (
            io.BytesIO((TRANSPORT_HEADER + transport_row()).encode()),
            'transportation.csv', 'text/csv'),
        'student_contacts_file': (
            io.BytesIO((CONTACT_HEADER + contact_row()).encode()),
            'student-contacts.csv', 'text/csv'),
    }
    partial = logged_in_client.post(
        '/admin/notifications/powerschool/preview', data=base,
        content_type='multipart/form-data')
    assert partial.status_code == 400
    assert 'both the Student Contacts and Guardian Contacts' in (
        partial.get_json()['message'])

    mixed = {
        '_csrf': csrf_token(logged_in_client), 'school_year': '2026-27',
        'snapshot_type': 'delta', 'mapping_profile_id': str(profile_id()),
        'transportation_file': (
            io.BytesIO((TRANSPORT_HEADER + transport_row()).encode()),
            'transportation.csv', 'text/csv'),
        'contacts_file': (
            io.BytesIO((CONTACT_HEADER + contact_row()).encode()),
            'contacts.csv', 'text/csv'),
        'student_contacts_file': (
            io.BytesIO((CONTACT_HEADER + contact_row()).encode()),
            'student-contacts.csv', 'text/csv'),
    }
    mixed_response = logged_in_client.post(
        '/admin/notifications/powerschool/preview', data=mixed,
        content_type='multipart/form-data')
    assert mixed_response.status_code == 400
    assert 'not both' in mixed_response.get_json()['message']


def test_invalid_stable_identity_is_rejected_and_report_formula_safe(logged_in_client):
    setup_route()
    response = preview(
        logged_in_client, transport_row('0001', school='=CMD'),
        contact_row('0001', '@CONTACT'))
    assert response.status_code == 200
    report = response.get_json()
    assert report['rejected'] >= 1
    exported = logged_in_client.get(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/report.csv")
    assert "'=CMD" in exported.get_data(as_text=True)


def test_later_manual_edit_blocks_rollback(logged_in_client):
    setup_route()
    report = preview(logged_in_client, transport_row(), contact_row()).get_json()
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        subscriber = application.NotificationSubscriber.query.one()
        subscriber.notes = 'Manual operator edit after import'
        application.db.session.commit()
    rollback = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert rollback.status_code == 409
    with application.app.app_context():
        assert application.NotificationSubscriber.query.one().notes == (
            'Manual operator edit after import')


def test_import_preview_masks_pii_for_explicitly_limited_operator(client):
    setup_route()
    with application.app.app_context():
        group = add_group('Masked PowerSchool Operators', {'notifications': 'full'})
        application._sync_group_capabilities(group.id)
        pii = application.GroupCapability.query.filter_by(
            group_id=group.id, capability_key='notifications.pii').one()
        pii.granted = False
        application.db.session.add(application.GroupCapability(
            group_id=group.id, capability_key='import.powerschool', granted=True))
        add_user('masked-ps', group)
        application.db.session.commit()
    login(client, 'masked-ps', 'Another-Safe-Password')
    report = preview(client, transport_row(), contact_row()).get_json()
    row = next(item for item in report['rows'] if item['classification'] == 'new')
    assert row['external_key'] == '••••0001'
    assert row['data']['student_number'] == '••••0001'
    assert row['data']['student_id'] == '••••0001'
    assert row['data']['household_id'] == '••••0001'
    assert row['data']['stop'] == '***'
    assert row['data']['first_name'] != 'Ada'
    assert row['data']['contacts'][0]['contact_id'] == '••••C-1'
    assert row['data']['contacts'][0]['email'] != 'grace@example.test'
    assert row['data']['contacts'][0]['phone'].endswith('0101')


def test_deactivation_candidates_mask_all_student_numbers_for_limited_operator(client):
    setup_route()
    with application.app.app_context():
        group = add_group('Masked Deactivation Operators', {'notifications': 'full'})
        application._sync_group_capabilities(group.id)
        application.GroupCapability.query.filter_by(
            group_id=group.id, capability_key='notifications.pii').one().granted = False
        application.db.session.add(application.GroupCapability(
            group_id=group.id, capability_key='import.powerschool', granted=True))
        add_user('masked-deactivation', group)
        application.db.session.commit()
    login(client, 'masked-deactivation', 'Another-Safe-Password')
    first = preview_v2(
        client, transport_v2_row('0001'),
        contact_row('0001', 'C-1')).get_json()
    assert apply_batch(client, first).status_code == 200

    second = preview_v2(
        client, transport_v2_row('0002', 'Alan', 'Turing'),
        contact_row('0002', 'C-2'), snapshot='full_district').get_json()
    candidate = next(row for row in second['rows']
                     if row['classification'] == 'deactivate_candidate')
    assert candidate['data']['student_numbers'] == ['••••0001']
    assert '0001' not in candidate['data']['student_numbers']


def test_retention_purges_normalized_pii_and_closes_rollback(logged_in_client):
    setup_route()
    report = preview(logged_in_client, transport_row(), contact_row()).get_json()
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        batch.applied_at = application._utcnow() - timedelta(days=31)
        application.db.session.commit()
        application._cleanup_import_stages()
        batch = application.db.session.get(application.ImportBatch, batch.id)
        assert batch.status == 'retention_closed'
        row = application.ImportRow.query.filter_by(batch_id=batch.id).first()
        retained = json.loads(row.normalized_json)
        assert retained['retained'] is True
        assert retained['external_key_sha256'] == hashlib.sha256(
            b'0001').hexdigest()
        assert row.external_key is None
        assert all(change.before_json is None and change.after_json is None
                   for change in application.ImportChange.query.filter_by(
                       batch_id=batch.id).all())
        assert json.loads(batch.metadata_json)['pii_purged_at']

    exported = logged_in_client.get(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/report.csv")
    assert exported.status_code == 200
    csv_rows = list(csv.DictReader(io.StringIO(
        exported.get_data(as_text=True).lstrip('\ufeff'))))
    assert csv_rows
    assert all(row['external_key'] == '' for row in csv_rows)
    assert '0001' not in exported.get_data(as_text=True)


def test_retention_also_purges_failed_rollback_pii(logged_in_client):
    setup_route()
    report = preview(logged_in_client, transport_row(), contact_row()).get_json()
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        batch.status = 'rollback_failed'
        batch.applied_at = application._utcnow() - timedelta(days=31)
        application.db.session.commit()
        application._cleanup_import_stages()
        batch = application.db.session.get(application.ImportBatch, batch.id)
        assert batch.status == 'retention_closed'
        assert json.loads(application.ImportRow.query.filter_by(
            batch_id=batch.id).first().normalized_json)['retained'] is True
        assert all(change.before_json is None and change.after_json is None
                   for change in application.ImportChange.query.filter_by(
                       batch_id=batch.id).all())
        assert json.loads(batch.metadata_json)['pii_purged_at']


def test_retention_retries_raw_file_cleanup_before_closing(logged_in_client, monkeypatch):
    setup_route()
    report = preview(logged_in_client, transport_row(), contact_row()).get_json()

    original_remove = application.os.remove
    def fail_remove(_path):
        raise OSError('forced cleanup failure')

    monkeypatch.setattr(application.os, 'remove', fail_remove)
    applied = apply_batch(logged_in_client, report)
    assert applied.status_code == 200
    assert applied.get_json()['cleanup_warnings'] == 2

    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        batch.applied_at = application._utcnow() - timedelta(days=31)
        batch.expires_at = application._utcnow() + timedelta(days=1)
        application.db.session.commit()
        application._cleanup_import_stages()
        batch = application.db.session.get(application.ImportBatch, batch.id)
        assert batch.status == 'applied'
        assert application.ImportFile.query.filter_by(batch_id=batch.id).count() == 2
        assert not json.loads(batch.metadata_json).get('pii_purged_at')
        assert application.ImportChange.query.filter_by(
            batch_id=batch.id).first().after_json is not None

    monkeypatch.setattr(application.os, 'remove', original_remove)
    with application.app.app_context():
        application._cleanup_import_stages()
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        assert batch.status == 'retention_closed'
        assert application.ImportFile.query.filter_by(batch_id=batch.id).count() == 0
        assert json.loads(batch.metadata_json)['pii_purged_at']
        assert all(change.before_json is None and change.after_json is None
                   for change in application.ImportChange.query.filter_by(
                       batch_id=batch.id).all())


def test_templates_compile_and_do_not_use_dynamic_inner_html():
    with application.app.app_context():
        application.app.jinja_env.get_template('admin/powerschool_import.html')
        application.app.jinja_env.get_template('admin/powerschool_guide.html')
    source = open('templates/admin/powerschool_import.html', encoding='utf-8').read()
    assert 'innerHTML' not in source
    assert 'eval(' not in source
    assert "batch.rows.filter(row => row.selected)" in source
    assert "document.querySelectorAll('[data-import-row]:checked')" not in source
    assert 'const refreshed = await requestJson' in source
    assert 'let selectionDirty = false' in source
    assert 'Selection changed. Save the selection before applying the plan.' in source
    assert 'if (selectionDirty)' in source


def test_test_database_override_rejects_non_disposable_targets(monkeypatch):
    monkeypatch.setenv('TEST_DATABASE_URL', 'sqlite:////var/tmp/shared.db')
    with pytest.raises(RuntimeError, match='generated TEST_ROOT'):
        _database_url_for_tests()
    monkeypatch.setenv(
        'TEST_DATABASE_URL',
        'postgresql://tester:synthetic@127.0.0.1:5432/production')
    monkeypatch.setenv('D205_ALLOW_DESTRUCTIVE_TEST_DATABASE', '1')
    with pytest.raises(RuntimeError, match='d205_test_'):
        _database_url_for_tests()
    monkeypatch.setenv(
        'TEST_DATABASE_URL',
        'postgresql://tester:synthetic@db.internal:5432/d205_test_candidate')
    with pytest.raises(RuntimeError, match='loopback'):
        _database_url_for_tests()


@pytest.mark.parametrize('query', [
    'host=prod-db.example',
    'hostaddr=203.0.113.10',
    'dbname=production',
    'service=production',
])
def test_test_database_override_rejects_query_target_overrides(monkeypatch, query):
    monkeypatch.setenv(
        'TEST_DATABASE_URL',
        f'postgresql://tester:synthetic@127.0.0.1:5432/d205_test_candidate?{query}')
    monkeypatch.setenv('D205_ALLOW_DESTRUCTIVE_TEST_DATABASE', '1')
    with pytest.raises(RuntimeError, match='query parameters'):
        _database_url_for_tests()


def test_mapping_profile_resolves_names_not_column_positions():
    mapping = {
        'files': {
            'transportation': {
                'required': ['student_number', 'route'],
                'columns': {'student_number': ['sid'], 'route': ['bus'],
                            'first_name': ['given'], 'last_name': ['surname'],
                            'period': ['run']},
            },
            'contacts': {
                'required': ['student_number', 'contact_id'],
                'columns': {'student_number': ['sid'], 'contact_id': ['cid'],
                            'first_name': ['given'], 'email': ['mail']},
            },
        },
        'period_aliases': {'AM': ['MORNING']},
    }
    transport = b'surname,bus,sid,run,given\nLovelace,TEST1,0001,MORNING,Ada\n'
    contacts = b'mail,given,cid,sid\ngr@example.test,Grace,C-1,0001\n'
    result = build_normalized_plan(transport, contacts, mapping, 10, 10)
    assert result['students'][0]['student_number'] == '0001'
    assert result['students'][0]['assignments'][0]['period'] == 'AM'
    assert result['students'][0]['contacts'][0]['contact_id'] == 'C-1'


def test_concurrent_apply_claim_creates_one_enrollment(logged_in_client):
    setup_route()
    report = preview(logged_in_client, transport_row(), contact_row()).get_json()

    def submit_once(_):
        client = application.app.test_client()
        login(client)
        return client.post(
            f"/admin/notifications/powerschool/batch/{report['batch_id']}/apply",
            json={'plan_hash': report['plan_hash']},
            headers={'X-CSRF-Token': csrf_token(client)}).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(submit_once, range(4)))
    assert 200 in statuses
    assert set(statuses) <= {200, 409}
    with application.app.app_context():
        assert application.NotificationSubscriber.query.count() == 1
        assert application.ExternalIdentity.query.filter_by(
            entity_type='student', external_key='0001').count() == 1
