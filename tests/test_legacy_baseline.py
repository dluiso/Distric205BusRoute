import json
from datetime import timedelta

import pytest

import app as application
from conftest import csrf_token
from test_phase3_powerschool import (
    apply_batch,
    contact_row,
    preview_v2,
    setup_route,
    transport_v2_row,
)


LEGACY_HEADER = (
    'subscriber_id,household_label,group,active,role,first_name,last_name,'
    'email,phone\n'
)


@pytest.fixture(autouse=True)
def enable_powerschool():
    previous = application.app.config['POWERSCHOOL_IMPORT_ENABLED']
    application.app.config['POWERSCHOOL_IMPORT_ENABLED'] = True
    yield
    application.app.config['POWERSCHOOL_IMPORT_ENABLED'] = previous


def _seed_historical_roster(tmp_path, duplicate_candidate=False):
    source_path = tmp_path / 'original-legacy.csv'
    source_path.write_text(
        LEGACY_HEADER
        + ',Historical Household,TEST 1 AM PM,yes,parent,Grace,Hopper,'
          'GRACE@EXAMPLE.TEST,708-555-0101\n'
        + ',Historical Household,TEST 1 AM PM,yes,student,Ada,Lovelace,'
          'ada@example.test,\n',
        encoding='utf-8',
    )
    with application.app.app_context():
        morning = application.BusScheduleType.query.filter_by(
            name='Morning').one()
        afternoon = application.BusScheduleType.query.filter_by(
            name='Afternoon').one()
        bus = application.Bus.query.filter_by(
            identifier='TEST', name='1').one()
        group = application.SubscriberGroup(name='TEST 1 AM PM')
        application.db.session.add(group)
        application.db.session.flush()
        application.db.session.add_all([
            application.GroupBusAssignment(
                group_id=group.id, bus_id=bus.id,
                schedule_type_id=morning.id),
            application.GroupBusAssignment(
                group_id=group.id, bus_id=bus.id,
                schedule_type_id=afternoon.id),
        ])

        def add_candidate():
            subscriber = application.NotificationSubscriber(
                notes='Historical Household', group_id=group.id, active=True)
            application.db.session.add(subscriber)
            application.db.session.flush()
            application.db.session.add_all([
                application.SubscriberContact(
                    subscriber_id=subscriber.id, sort_order=0,
                    first_name='Grace', last_name='Hopper',
                    email='GRACE@EXAMPLE.TEST', phone='708-555-0101',
                    role='parent'),
                application.SubscriberContact(
                    subscriber_id=subscriber.id, sort_order=1,
                    first_name='Ada', last_name='Lovelace',
                    email='ada@example.test', phone=None, role='student'),
            ])
            return subscriber

        candidate = add_candidate()
        if duplicate_candidate:
            add_candidate()
        manual = application.NotificationSubscriber(
            notes='Operator-created subscriber', active=True)
        application.db.session.add(manual)
        application.db.session.commit()
        return str(source_path), candidate.id, manual.id


def _invoke_baseline(source_path, *extra):
    return application.app.test_cli_runner().invoke(
        args=['adopt-legacy-baseline', source_path, *extra])


def _apply_args(summary, **overrides):
    values = {
        'source_sha': summary['source_sha256'],
        'manifest_sha': summary['manifest_sha256'],
        'candidates': summary['candidate_count'],
        'contacts': summary['contact_count'],
        'groups': summary['group_count'],
        'preserved': summary['preserved_count'],
        'approved_by': 'admin',
    }
    values.update(overrides)
    return [
        '--apply', '--source-sha', str(values['source_sha']),
        '--manifest-sha', str(values['manifest_sha']),
        '--expected-candidates', str(values['candidates']),
        '--expected-contacts', str(values['contacts']),
        '--expected-groups', str(values['groups']),
        '--expected-preserved', str(values['preserved']),
        '--approved-by', str(values['approved_by']),
    ]


def test_unmanaged_roster_blocks_then_exact_baseline_enables_safe_cutover(
        logged_in_client, tmp_path):
    setup_route()
    source_path, candidate_id, manual_id = _seed_historical_roster(tmp_path)

    staged = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(), snapshot='delta')
    assert staged.status_code == 200
    staged_report = staged.get_json()
    assert staged_report['legacy_cutover']['baseline_required'] is True
    assert staged_report['legacy_cutover']['baseline_available'] is True
    assert staged_report['legacy_cutover']['unmanaged_count'] == 2
    selection = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/"
        f"{staged_report['batch_id']}/selection",
        json={
            'plan_hash': staged_report['plan_hash'],
            'selected_row_ids': [], 'deactivation_row_ids': [],
        },
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert selection.status_code == 409
    assert apply_batch(logged_in_client, staged_report).status_code == 409

    dry_run = _invoke_baseline(source_path)
    assert dry_run.exit_code == 0, dry_run.output
    summary = json.loads(dry_run.output)
    assert summary['candidate_count'] == 1
    assert summary['contact_count'] == 2
    assert summary['group_count'] == 1
    assert summary['preserved_count'] == 1
    assert len(summary['source_sha256']) == 64
    assert len(summary['manifest_sha256']) == 64
    assert 'Grace' not in dry_run.output
    assert 'EXAMPLE.TEST' not in dry_run.output

    mismatch = _invoke_baseline(
        source_path, *_apply_args(
            summary, preserved=summary['preserved_count'] + 1))
    assert mismatch.exit_code != 0
    bad_source_sha = _invoke_baseline(
        source_path, *_apply_args(summary, source_sha='0' * 64))
    assert bad_source_sha.exit_code != 0
    bad_manifest = _invoke_baseline(
        source_path, *_apply_args(summary, manifest_sha='0' * 64))
    assert bad_manifest.exit_code != 0
    with application.app.app_context():
        assert application.ImportBatch.query.filter_by(
            schema_version=application.LEGACY_BASELINE_SCHEMA_VERSION,
        ).count() == 0
        assert application.AuditLog.query.filter_by(
            action='legacy_baseline_adopted').count() == 0

    applied = _invoke_baseline(source_path, *_apply_args(summary))
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)['already_applied'] is False
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            schema_version=application.LEGACY_BASELINE_SCHEMA_VERSION,
        ).one()
        changes = application.ImportChange.query.filter_by(
            batch_id=batch.id).order_by(application.ImportChange.target_id).all()
        assert {change.operation for change in changes} == {
            'adopt_legacy_ownership', 'preserve_manual'}
        assert {change.target_id for change in changes} == {
            candidate_id, manual_id}
        assert all('email' not in (change.after_json or '')
                   and 'first_name' not in (change.after_json or '')
                   for change in changes)
        assert application.AuditLog.query.filter_by(
            action='legacy_baseline_adopted', target=batch.public_id).one()

    replay = _invoke_baseline(source_path, *_apply_args(summary))
    assert replay.exit_code == 0, replay.output
    assert json.loads(replay.output)['already_applied'] is True
    with application.app.app_context():
        assert application.ImportBatch.query.filter_by(
            schema_version=application.LEGACY_BASELINE_SCHEMA_VERSION,
        ).count() == 1

    refreshed = logged_in_client.get(
        f"/admin/notifications/powerschool/batch/{staged_report['batch_id']}")
    assert refreshed.status_code == 200
    assert refreshed.get_json()['legacy_cutover']['requires_reanalysis'] is True

    full = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(),
        snapshot='full_district').get_json()
    assert full['legacy_cutover']['baseline_required'] is False
    assert full['legacy_cutover']['candidate_count'] == 1
    new_row = next(row for row in full['rows']
                   if row['classification'] == 'new')
    approved = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{full['batch_id']}/selection",
        json={
            'plan_hash': full['plan_hash'],
            'selected_row_ids': [new_row['id']],
            'deactivation_row_ids': [],
            'confirm_deactivations': False,
            'legacy_cutover_approved': True,
        },
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert approved.status_code == 200, approved.get_data(as_text=True)
    full['plan_hash'] = approved.get_json()['plan_hash']
    assert apply_batch(logged_in_client, full).status_code == 200
    with application.app.app_context():
        assert application.db.session.get(
            application.NotificationSubscriber, candidate_id).active is False
        assert application.db.session.get(
            application.NotificationSubscriber, manual_id).active is True
    rollback = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{full['batch_id']}/rollback",
        json={}, headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert rollback.status_code == 200
    with application.app.app_context():
        assert application.db.session.get(
            application.NotificationSubscriber, candidate_id).active is True
        assert application.db.session.get(
            application.NotificationSubscriber, manual_id).active is True


def test_baseline_rejects_mutated_source_and_ambiguous_database_match(tmp_path):
    setup_route()
    source_path, _, _ = _seed_historical_roster(
        tmp_path, duplicate_candidate=True)
    ambiguous = _invoke_baseline(source_path)
    assert ambiguous.exit_code != 0
    assert 'one-to-one' in ambiguous.output

    with open(source_path, 'a', encoding='utf-8') as handle:
        handle.write(
            ',Missing Household,UNKNOWN,yes,parent,Extra,Person,'
            'extra@example.test,\n')
    mutated = _invoke_baseline(source_path)
    assert mutated.exit_code != 0
    assert 'cannot be resolved exactly' in mutated.output


def test_baseline_apply_revalidates_manifest_admin_and_current_state(tmp_path):
    setup_route()
    source_path, candidate_id, _ = _seed_historical_roster(tmp_path)
    summary = json.loads(_invoke_baseline(source_path).output)

    inactive_admin = _invoke_baseline(
        source_path, *_apply_args(summary, approved_by='missing-admin'))
    assert inactive_admin.exit_code != 0
    with application.app.app_context():
        contact = application.SubscriberContact.query.filter_by(
            subscriber_id=candidate_id, sort_order=0).one()
        contact.phone = 'changed-after-dry-run'
        application.db.session.commit()
    changed = _invoke_baseline(source_path, *_apply_args(summary))
    assert changed.exit_code != 0
    with application.app.app_context():
        assert application.ImportBatch.query.filter_by(
            schema_version=application.LEGACY_BASELINE_SCHEMA_VERSION,
        ).count() == 0


def test_partial_or_corrupt_prior_baseline_fails_closed(tmp_path):
    setup_route()
    source_path, _, _ = _seed_historical_roster(tmp_path)
    with application.app.app_context():
        owner = application.User.query.filter_by(username='admin').one()
        now = application._utcnow()
        application.db.session.add(application.ImportBatch(
            public_id='partial-baseline', source_type='legacy_csv',
            schema_version=application.LEGACY_BASELINE_SCHEMA_VERSION,
            status='failed', snapshot_type='delta', uploaded_by_id=owner.id,
            file_sha256='a' * 64, analysis_context_sha256='b' * 64,
            plan_hash='b' * 64, total_rows=0, selected_rows=0,
            rejected_rows=0, excluded_rows=0, metadata_json='{}',
            created_at=now, expires_at=now + timedelta(days=1)))
        application.db.session.commit()
    result = _invoke_baseline(source_path)
    assert result.exit_code != 0
    assert 'partial provenance baseline' in result.output


def test_baseline_refuses_active_powerschool_identity(tmp_path):
    setup_route()
    source_path, candidate_id, _ = _seed_historical_roster(tmp_path)
    with application.app.app_context():
        application.db.session.add(application.ExternalIdentity(
            source_type='powerschool', entity_type='student',
            external_key='existing-student',
            local_table='notification_subscriber', local_id=candidate_id))
        application.db.session.commit()
    result = _invoke_baseline(source_path)
    assert result.exit_code != 0
    assert 'PowerSchool roster is already active' in result.output


def test_baseline_transaction_rolls_back_batch_changes_and_audit(
        tmp_path, monkeypatch):
    setup_route()
    source_path, _, _ = _seed_historical_roster(tmp_path)
    summary = json.loads(_invoke_baseline(source_path).output)
    original_commit = application.db.session.commit

    def fail_commit():
        raise RuntimeError('forced commit failure')

    monkeypatch.setattr(application.db.session, 'commit', fail_commit)
    result = _invoke_baseline(source_path, *_apply_args(summary))
    assert result.exit_code != 0
    monkeypatch.setattr(application.db.session, 'commit', original_commit)
    with application.app.app_context():
        application.db.session.remove()
        assert application.ImportBatch.query.filter_by(
            schema_version=application.LEGACY_BASELINE_SCHEMA_VERSION,
        ).count() == 0
        assert application.ImportChange.query.filter(
            application.ImportChange.operation.in_([
                'adopt_legacy_ownership', 'preserve_manual',
            ])).count() == 0
        assert application.AuditLog.query.filter_by(
            action='legacy_baseline_adopted').count() == 0


def test_baseline_replay_rejects_corrupt_change_record(tmp_path):
    setup_route()
    source_path, _, _ = _seed_historical_roster(tmp_path)
    summary = json.loads(_invoke_baseline(source_path).output)
    assert _invoke_baseline(
        source_path, *_apply_args(summary)).exit_code == 0
    with application.app.app_context():
        change = application.ImportChange.query.filter_by(
            operation='adopt_legacy_ownership').one()
        record = json.loads(change.after_json)
        record.pop('created_at')
        change.after_json = json.dumps(record, sort_keys=True)
        application.db.session.commit()
    replay = _invoke_baseline(source_path, *_apply_args(summary))
    assert replay.exit_code != 0
    with application.app.app_context():
        assert application.ImportBatch.query.filter_by(
            schema_version=application.LEGACY_BASELINE_SCHEMA_VERSION,
        ).count() == 1


def test_rolled_back_powerschool_history_does_not_bypass_or_block_baseline(
        logged_in_client, tmp_path):
    setup_route()
    source_path, _, _ = _seed_historical_roster(tmp_path)
    with application.app.app_context():
        owner = application.User.query.filter_by(username='admin').one()
        now = application._utcnow()
        application.db.session.add(application.ImportBatch(
            public_id='fully-rolled-back-powerschool',
            source_type='powerschool', schema_version='1',
            status='rolled_back', snapshot_type='delta',
            uploaded_by_id=owner.id, file_sha256='a' * 64,
            analysis_context_sha256='b' * 64, plan_hash='c' * 64,
            total_rows=1, selected_rows=1, rejected_rows=0,
            excluded_rows=0, metadata_json='{}', created_at=now,
            applied_at=now, expires_at=now + timedelta(days=1)))
        application.db.session.commit()

    report = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(),
        snapshot='delta').get_json()
    assert report['legacy_cutover']['baseline_required'] is True
    assert report['legacy_cutover']['baseline_available'] is True
    assert report['legacy_cutover']['requires_reanalysis'] is True
    assert apply_batch(logged_in_client, report).status_code == 409
    dry_run = _invoke_baseline(source_path)
    assert dry_run.exit_code == 0, dry_run.output
    assert json.loads(dry_run.output)['candidate_count'] == 1


def test_corrupt_applied_baseline_blocks_ordinary_cutover(
        logged_in_client, tmp_path):
    setup_route()
    source_path, _, _ = _seed_historical_roster(tmp_path)
    summary = json.loads(_invoke_baseline(source_path).output)
    assert _invoke_baseline(
        source_path, *_apply_args(summary)).exit_code == 0
    with application.app.app_context():
        batch = application.ImportBatch.query.filter_by(
            schema_version=application.LEGACY_BASELINE_SCHEMA_VERSION,
        ).one()
        metadata = json.loads(batch.metadata_json)
        metadata['candidate_count'] += 1
        batch.metadata_json = json.dumps(metadata, sort_keys=True)
        application.db.session.commit()

    report = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(),
        snapshot='full_district').get_json()
    assert report['legacy_cutover']['baseline_required'] is True
    assert report['legacy_cutover']['blocked'] is True
    assert report['legacy_cutover']['requires_reanalysis'] is True
    assert apply_batch(logged_in_client, report).status_code == 409


def test_mixed_powerschool_and_unmanaged_roster_still_fails_closed(
        logged_in_client):
    setup_route()
    with application.app.app_context():
        powerschool = application.NotificationSubscriber(
            notes='PowerSchool-owned', active=True)
        unmanaged = application.NotificationSubscriber(
            notes='Unclassified historical row', active=True)
        application.db.session.add_all([powerschool, unmanaged])
        application.db.session.flush()
        application.db.session.add(application.ExternalIdentity(
            source_type='powerschool', entity_type='student',
            external_key='already-owned',
            local_table='notification_subscriber',
            local_id=powerschool.id,
            created_at=max(application._utcnow(), powerschool.created_at)))
        application.db.session.commit()
        state = application._active_non_powerschool_roster_state()
        assert state['powerschool_active_count'] == 1
        assert [item.id for item in state['unmanaged']] == [unmanaged.id]

    response = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(), snapshot='delta')
    assert response.status_code == 200
    report = response.get_json()
    assert report['legacy_cutover']['baseline_required'] is True
    assert report['legacy_cutover']['baseline_available'] is False
    assert report['legacy_cutover']['blocked'] is True
    selection = logged_in_client.post(
        f"/admin/notifications/powerschool/batch/{report['batch_id']}/selection",
        json={
            'plan_hash': report['plan_hash'],
            'selected_row_ids': [
                row['id'] for row in report['rows']
                if row['classification'] in {'new', 'update'}
            ],
            'deactivation_row_ids': [],
        },
        headers={'X-CSRF-Token': csrf_token(logged_in_client)})
    assert selection.status_code == 409
    assert apply_batch(logged_in_client, report).status_code == 409


def test_manual_http_add_has_durable_provenance_and_does_not_block_updates(
        logged_in_client):
    setup_route()
    first = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(),
        snapshot='delta').get_json()
    assert apply_batch(logged_in_client, first).status_code == 200

    added = logged_in_client.post('/admin/notifications/add', data={
        '_csrf': csrf_token(logged_in_client),
        'notes': 'Operator-created enrollment',
        'group_id': '',
        'contact_count': '1',
        'contact_0_first_name': 'Manual',
        'contact_0_last_name': 'Person',
        'contact_0_email': 'manual@example.test',
        'contact_0_phone': '',
        'contact_0_role': 'parent',
    })
    assert added.status_code == 302
    with application.app.app_context():
        manual = application.NotificationSubscriber.query.filter_by(
            notes='Operator-created enrollment').one()
        manual_id = manual.id
        state = application._active_non_powerschool_roster_state()
        assert [item.id for item in state['manual']] == [manual_id]
        assert state['unmanaged'] == []
        assert state['conflicts'] == []

    update = preview_v2(
        logged_in_client, transport_v2_row(),
        contact_row(first='Katherine'), snapshot='delta').get_json()
    assert update['legacy_cutover']['baseline_required'] is False
    assert update['legacy_cutover']['unmanaged_count'] == 0
    assert apply_batch(logged_in_client, update).status_code == 200
    with application.app.app_context():
        assert application.db.session.get(
            application.NotificationSubscriber, manual_id).active is True


def test_stale_powerschool_identity_does_not_own_reused_subscriber_id(
        logged_in_client):
    setup_route()
    with application.app.app_context():
        original = application.NotificationSubscriber(
            notes='Original PowerSchool row', active=True)
        application.db.session.add(original)
        application.db.session.flush()
        original_id = original.id
        identity = application.ExternalIdentity(
            source_type='powerschool', entity_type='student',
            external_key='0001', local_table='notification_subscriber',
            local_id=original_id,
            created_at=max(application._utcnow(), original.created_at))
        application.db.session.add(identity)
        application.db.session.commit()
        replacement_created_at = identity.created_at + timedelta(seconds=1)
        application.db.session.delete(original)
        application.db.session.commit()
        replacement = application.NotificationSubscriber(
            id=original_id, notes='Reused unmanaged row', active=True,
            created_at=replacement_created_at)
        application.db.session.add(replacement)
        application.db.session.commit()
        state = application._active_non_powerschool_roster_state()
        assert state['powerschool_active_count'] == 0
        assert [item.id for item in state['unmanaged']] == [original_id]

    report = preview_v2(
        logged_in_client, transport_v2_row(), contact_row(),
        snapshot='delta').get_json()
    assert report['legacy_cutover']['baseline_required'] is True
    assert report['legacy_cutover']['blocked'] is True
    assert apply_batch(logged_in_client, report).status_code == 409


def test_delete_bulk_delete_and_contact_edit_remove_stale_identity_mappings(
        logged_in_client):
    with application.app.app_context():
        subscribers = []
        for index in range(3):
            subscriber = application.NotificationSubscriber(
                notes=f'Enrollment {index}', active=True)
            application.db.session.add(subscriber)
            application.db.session.flush()
            contact = application.SubscriberContact(
                subscriber_id=subscriber.id, first_name='Old',
                email=f'old-{index}@example.test', role='parent')
            application.db.session.add(contact)
            application.db.session.flush()
            application.db.session.add_all([
                application.ExternalIdentity(
                    source_type='powerschool', entity_type='student',
                    external_key=f'student-{index}',
                    local_table='notification_subscriber',
                    local_id=subscriber.id,
                    created_at=max(application._utcnow(), subscriber.created_at)),
                application.ExternalIdentity(
                    source_type='powerschool', entity_type='contact',
                    external_key=f'student-{index}|contact-{index}',
                    local_table='subscriber_contact', local_id=contact.id),
            ])
            subscribers.append((subscriber.id, contact.id))
        application.db.session.commit()

    deleted = logged_in_client.post(
        f'/admin/notifications/{subscribers[0][0]}/delete',
        data={'_csrf': csrf_token(logged_in_client)})
    assert deleted.status_code == 302
    bulk_deleted = logged_in_client.post(
        '/admin/notifications/bulk-delete',
        data={
            '_csrf': csrf_token(logged_in_client),
            'subscriber_ids': [str(subscribers[1][0])],
        })
    assert bulk_deleted.status_code == 302

    edited = logged_in_client.post(
        f'/admin/notifications/{subscribers[2][0]}/edit', data={
            '_csrf': csrf_token(logged_in_client),
            'notes': 'Edited enrollment',
            'group_id': '',
            'active': 'on',
            'contact_count': '1',
            'contact_0_first_name': 'Replacement',
            'contact_0_last_name': 'Person',
            'contact_0_email': 'replacement@example.test',
            'contact_0_phone': '',
            'contact_0_role': 'parent',
        })
    assert edited.status_code == 302
    with application.app.app_context():
        assert application.ExternalIdentity.query.filter(
            application.ExternalIdentity.external_key.in_([
                'student-0', 'student-0|contact-0',
                'student-1', 'student-1|contact-1',
            ])).count() == 0
        assert application.ExternalIdentity.query.filter_by(
            external_key='student-2').one()
        assert application.ExternalIdentity.query.filter_by(
            external_key='student-2|contact-2').count() == 0
        replacement = application.SubscriberContact.query.filter_by(
            subscriber_id=subscribers[2][0]).one()
        assert replacement.first_name == 'Replacement'
