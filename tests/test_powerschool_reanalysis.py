import io
import json

import pytest

import app as application
from conftest import csrf_token, login
from test_phase3_powerschool import (
    CONTACT_HEADER,
    TRANSPORT_HEADER,
    apply_batch,
    contact_row,
    profile_id,
    setup_route,
    transport_row,
)


V2_TRANSPORT_HEADER = (
    'student_number,first_name,last_name,grade,transport_status,route_am,route_pm,'
    'school,student_id\n'
)


@pytest.fixture(autouse=True)
def enable_powerschool():
    previous = application.app.config['POWERSCHOOL_IMPORT_ENABLED']
    application.app.config['POWERSCHOOL_IMPORT_ENABLED'] = True
    yield
    application.app.config['POWERSCHOOL_IMPORT_ENABLED'] = previous


def preview_with_options(client, transportation, contacts, *,
                         snapshot='delta', school_year='2026-27',
                         force_reanalyze=False):
    data = {
        '_csrf': csrf_token(client),
        'school_year': school_year,
        'snapshot_type': snapshot,
        'mapping_profile_id': str(profile_id()),
        'transportation_file': (
            io.BytesIO((TRANSPORT_HEADER + transportation).encode()),
            'transportation.csv', 'text/csv'),
        'contacts_file': (
            io.BytesIO((CONTACT_HEADER + contacts).encode()),
            'contacts.csv', 'text/csv'),
    }
    if force_reanalyze:
        data['force_reanalyze'] = '1'
    return client.post(
        '/admin/notifications/powerschool/preview', data=data,
        content_type='multipart/form-data')


def test_zero_valid_transportation_fails_preflight_without_persisting_batch(
        logged_in_client):
    invalid_transport = (
        '0001,SID-0001,HH-0001,Ada,Lovelace,205,5,,,AM,active,'
        '2026-27,T-0001-AM\n')

    response = preview_with_options(
        logged_in_client, invalid_transport, contact_row())

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['code'] == 'no_valid_transportation_rows'
    assert 'D205 BusRoute - Transportation v2' in payload['message']
    assert payload['preflight']['valid_transport_rows'] == 0
    assert payload['metrics']['contacts']['not_processed_rows'] == 1
    with application.app.app_context():
        assert application.ImportBatch.query.count() == 0
        assert application.ImportFile.query.count() == 0
        assert application.ImportRow.query.count() == 0


def test_exact_context_can_open_existing_or_explicitly_reanalyze(
        logged_in_client):
    setup_route()
    first = preview_with_options(
        logged_in_client, transport_row(), contact_row())
    assert first.status_code == 200
    first_payload = first.get_json()

    duplicate = preview_with_options(
        logged_in_client, transport_row(), contact_row())
    assert duplicate.status_code == 409
    duplicate_payload = duplicate.get_json()
    assert duplicate_payload['existing_batch_id'] == first_payload['batch_id']
    assert duplicate_payload['existing_status'] == 'staged'
    assert duplicate_payload['can_open'] is True
    assert duplicate_payload['reanalyze_allowed'] is True

    reanalysis = preview_with_options(
        logged_in_client, transport_row(), contact_row(),
        force_reanalyze=True)
    assert reanalysis.status_code == 200
    reanalysis_payload = reanalysis.get_json()
    assert reanalysis_payload['batch_id'] != first_payload['batch_id']
    assert reanalysis_payload['reanalyzed_from'] == first_payload['batch_id']
    assert reanalysis_payload['normalizer_revision']
    with application.app.app_context():
        assert application.ImportBatch.query.count() == 2


def test_snapshot_policy_is_part_of_analysis_context(logged_in_client):
    setup_route()
    delta = preview_with_options(
        logged_in_client, transport_row(), contact_row(), snapshot='delta')
    complete = preview_with_options(
        logged_in_client, transport_row(), contact_row(),
        snapshot='full_district')

    assert delta.status_code == 200
    assert complete.status_code == 200
    assert delta.get_json()['batch_id'] != complete.get_json()['batch_id']


def test_transportation_v2_blocks_unproven_complete_snapshot_before_staging(
        logged_in_client):
    response = logged_in_client.post(
        '/admin/notifications/powerschool/preview',
        data={
            '_csrf': csrf_token(logged_in_client),
            'school_year': '2026-27',
            'snapshot_type': 'full_district',
            'mapping_profile_id': str(profile_id()),
            'transportation_file': (
                io.BytesIO((
                    V2_TRANSPORT_HEADER
                    + '0001,Ada,Lovelace,5,Active,TEST1 AM,,205,SID-0001\n'
                ).encode()),
                'D205_BusRoute_Transportation_v2.csv',
                'text/csv',
            ),
            'contacts_file': (
                io.BytesIO((CONTACT_HEADER + contact_row()).encode()),
                'contacts.csv',
                'text/csv',
            ),
        },
        content_type='multipart/form-data',
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['code'] == 'transportation_v2_full_snapshot_not_proven'
    assert 'Select Delta' in payload['message']
    assert payload['preflight']['dual_route'] is True
    with application.app.app_context():
        assert application.ImportBatch.query.count() == 0


def test_transportation_v2_clean_dual_route_snapshot_can_stage(
        logged_in_client):
    setup_route()
    response = logged_in_client.post(
        '/admin/notifications/powerschool/preview',
        data={
            '_csrf': csrf_token(logged_in_client),
            'school_year': '2026-27',
            'snapshot_type': 'full_district',
            'mapping_profile_id': str(profile_id()),
            'transportation_file': (
                io.BytesIO((
                    V2_TRANSPORT_HEADER
                    + '0001,Ada,Lovelace,5,Active,TEST1 AM,TEST1 PM,205,SID-0001\n'
                ).encode()),
                'D205_BusRoute_Transportation_v2.csv',
                'text/csv',
            ),
            'contacts_file': (
                io.BytesIO((CONTACT_HEADER + contact_row()).encode()),
                'contacts.csv',
                'text/csv',
            ),
        },
        content_type='multipart/form-data',
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload['preflight']['dual_route'] is True
    assert payload['metrics']['transportation']['period_am_rows'] == 1
    assert payload['metrics']['transportation']['period_pm_rows'] == 1


def test_transportation_v2_can_apply_different_am_and_pm_buses(
        logged_in_client):
    setup_route()
    with application.app.app_context():
        morning = application.BusScheduleType.query.filter_by(
            name='Morning').one()
        afternoon = application.BusScheduleType.query.filter_by(
            name='Afternoon').one()
        second_bus = application.Bus(
            identifier='TR', name='ALG1', route='Compound Test Route',
            active=True)
        application.db.session.add(second_bus)
        application.db.session.flush()
        application.db.session.add_all([
            application.BusScheduleAssignment(
                bus_id=second_bus.id, schedule_type_id=morning.id),
            application.BusScheduleAssignment(
                bus_id=second_bus.id, schedule_type_id=afternoon.id),
        ])
        application.db.session.commit()

    response = logged_in_client.post(
        '/admin/notifications/powerschool/preview',
        data={
            '_csrf': csrf_token(logged_in_client),
            'school_year': '2026-27',
            'snapshot_type': 'delta',
            'mapping_profile_id': str(profile_id()),
            'transportation_file': (
                io.BytesIO((
                    V2_TRANSPORT_HEADER
                    + '0001,Ada,Lovelace,5,Active,TEST1 AM,TR ALG1 PM,205,SID-0001\n'
                ).encode()),
                'D205_BusRoute_Transportation_v2.csv',
                'text/csv',
            ),
            'contacts_file': (
                io.BytesIO((CONTACT_HEADER + contact_row()).encode()),
                'contacts.csv',
                'text/csv',
            ),
        },
        content_type='multipart/form-data',
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    report = response.get_json()
    row = next(item for item in report['rows']
               if item['classification'] == 'new')
    assert row['data']['group_name'] == 'TEST1 AM / TRALG1 PM'
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        group = application.NotificationSubscriber.query.one().group
        assert {
            (assignment.bus.name, assignment.schedule_type.name)
            for assignment in group.bus_assignments
        } == {('1', 'Morning'), ('ALG1', 'Afternoon')}


def test_canonical_bus_lookup_handles_schedule_token_stored_as_bus_name(client):
    with application.app.test_request_context():
        morning = application.BusScheduleType.query.filter_by(
            name='Morning').one()
        afternoon = application.BusScheduleType.query.filter_by(
            name='Afternoon').one()
        bus = application.Bus(
            identifier='MCK1', name='AM', route='McKinley', active=True)
        application.db.session.add(bus)
        application.db.session.flush()
        application.db.session.add_all([
            application.BusScheduleAssignment(
                bus_id=bus.id, schedule_type_id=morning.id),
            application.BusScheduleAssignment(
                bus_id=bus.id, schedule_type_id=afternoon.id),
        ])
        application.db.session.commit()

        resolved = application._powerschool_bus_for_route('MCK', '1', 'PM')

        assert resolved is not None
        assert resolved.id == bus.id


def test_mapping_content_change_allows_fresh_analysis(logged_in_client):
    setup_route()
    first = preview_with_options(
        logged_in_client, transport_row(), contact_row())
    assert first.status_code == 200
    with application.app.app_context():
        profile = application.ImportMappingProfile.query.filter_by(
            source_type='powerschool', schema_version='1').one()
        mapping = json.loads(profile.mapping_json)
        mapping['files']['transportation']['columns']['route'].append(
            'district_custom_route_alias')
        profile.mapping_json = json.dumps(mapping)
        application.db.session.commit()

    second = preview_with_options(
        logged_in_client, transport_row(), contact_row())

    assert second.status_code == 200
    assert second.get_json()['batch_id'] != first.get_json()['batch_id']


def test_another_operator_is_not_blocked_or_given_an_existing_batch_id(
        logged_in_client):
    setup_route()
    first = preview_with_options(
        logged_in_client, transport_row(), contact_row())
    assert first.status_code == 200
    with application.app.app_context():
        group = application.UserGroup(
            name='PowerSchool Import Operators', is_admin=False)
        application.db.session.add(group)
        application.db.session.flush()
        application.db.session.add(application.GroupPermission(
            group_id=group.id, module_key='notifications', access_level='full'))
        application.db.session.add(application.GroupCapability(
            group_id=group.id, capability_key='import.powerschool', granted=True))
        operator = application.User(
            username='ps-operator', email='ps-operator@example.test',
            group_id=group.id, active=True)
        operator.set_password('Another-Safe-Password')
        application.db.session.add(operator)
        application.db.session.commit()
    second_client = application.app.test_client()
    login(second_client, 'ps-operator', 'Another-Safe-Password')

    second = preview_with_options(
        second_client, transport_row(), contact_row())

    assert second.status_code == 200
    assert second.get_json()['batch_id'] != first.get_json()['batch_id']


def test_reanalysis_is_blocked_while_existing_batch_is_applying(
        logged_in_client):
    setup_route()
    first = preview_with_options(
        logged_in_client, transport_row(), contact_row()).get_json()
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            public_id=first['batch_id']).one()
        batch.status = 'applying'
        application.db.session.commit()

    response = preview_with_options(
        logged_in_client, transport_row(), contact_row(),
        force_reanalyze=True)

    assert response.status_code == 409
    assert response.get_json()['reanalyze_allowed'] is False


def test_older_busy_same_context_blocks_reanalysis_despite_newer_stage(
        logged_in_client):
    setup_route()
    first = preview_with_options(
        logged_in_client, transport_row(), contact_row()).get_json()
    second = preview_with_options(
        logged_in_client, transport_row(), contact_row(),
        force_reanalyze=True).get_json()
    with application.app.app_context():
        first_batch = application.ImportBatch.query.filter_by(
            public_id=first['batch_id']).one()
        first_batch.status = 'applying'
        application.db.session.commit()

    response = preview_with_options(
        logged_in_client, transport_row(), contact_row(),
        force_reanalyze=True)

    assert response.status_code == 409
    payload = response.get_json()
    assert payload['existing_batch_id'] == first['batch_id']
    assert payload['existing_status'] == 'applying'
    assert payload['reanalyze_allowed'] is False
    with application.app.app_context():
        assert application.ImportBatch.query.count() == 2
        assert application.ImportBatch.query.filter_by(
            public_id=second['batch_id'], status='staged').one()


def test_rollback_preserves_email_history_and_detaches_imported_targets(
        logged_in_client):
    setup_route()
    report = preview_with_options(
        logged_in_client, transport_row(), contact_row()).get_json()
    assert apply_batch(logged_in_client, report).status_code == 200
    with application.app.app_context():
        subscriber = application.NotificationSubscriber.query.one()
        group = subscriber.group
        application.db.session.add(application.NotificationLog(
            channel='email', recipient_name='Imported recipient',
            recipient_address='grace@example.test', subscriber_id=subscriber.id,
            group_id=group.id, group_name=group.name, status='sent'))
        application.db.session.add(application.EmailOutbox(
            dedupe_key='test:import-rollback-history', kind='notification',
            recipient_name='Imported recipient',
            recipient_address='grace@example.test', subject='History',
            body='Retained delivery body', status='sent', attempts=1,
            subscriber_id=subscriber.id, group_id=group.id,
            group_name=group.name))
        application.db.session.commit()

    rollback = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})

    assert rollback.status_code == 200, rollback.get_data(as_text=True)
    with application.app.app_context():
        assert application.NotificationSubscriber.query.count() == 0
        assert application.SubscriberGroup.query.count() == 0
        log = application.NotificationLog.query.one()
        outbox = application.EmailOutbox.query.one()
        assert (log.subscriber_id, log.group_id, log.status) == (
            None, None, 'sent')
        assert (outbox.subscriber_id, outbox.group_id, outbox.status) == (
            None, None, 'sent')
        batch = application.ImportBatch.query.filter_by(
            public_id=report['batch_id']).one()
        metadata = json.loads(batch.metadata_json)
        assert metadata['detached_notification_history'] == {
            'email_outbox': 2,
            'notification_log': 2,
        }
