(function () {
  'use strict';

  const configNode = document.getElementById('bus-inventory-config');
  if (!configNode) return;
  const config = JSON.parse(configNode.textContent || '{}');
  const buses = Array.isArray(config.buses) ? config.buses : [];
  const busById = new Map(buses.map((bus) => [Number(bus.id), bus]));
  const validStates = new Set(['active', 'inactive', 'trash', 'attention', 'pending']);
  let stateMode = validStates.has(config.initialState) ? config.initialState : 'active';
  let drawerBusId = null;
  let drawerReturnFocus = null;
  let lifecycleContext = null;
  let selectionMode = false;
  const selectedBusIds = new Set();
  let viewMode = 'cards';
  const viewStorageKey = `${config.savedViewKey}:${window.matchMedia('(max-width: 767px)').matches ? 'mobile' : 'desktop'}`;
  try {
    const saved = localStorage.getItem(viewStorageKey);
    if (saved === 'cards' || saved === 'list') viewMode = saved;
  } catch (_error) { /* storage can be unavailable */ }

  const byId = (id) => document.getElementById(id);
  const search = byId('bus-search');
  const statusFilter = byId('bus-status-filter');
  const scheduleFilter = byId('bus-schedule-filter');
  const typeFilter = byId('bus-type-filter');
  const routeFilter = byId('bus-route-filter');
  const schoolFilter = byId('bus-school-filter');
  const groupFilter = byId('bus-group-filter');
  const sortControl = byId('bus-sort');
  const cardContainer = byId('bus-card-container');
  const listContainer = byId('bus-list-container');
  const listBody = byId('bus-list-body');
  const emptyState = byId('bus-empty-state');
  const resultCount = byId('bus-result-count');
  const drawer = byId('bus-detail-drawer');
  const drawerOverlay = byId('bus-drawer-overlay');
  const bulkBar = byId('bus-bulk-bar');
  const bulkModal = byId('bus-bulk-modal');

  function normalize(value) {
    return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }

  function compact(value) { return normalize(value).replace(/\s+/g, ''); }
  function naturalCompare(left, right) {
    return String(left || '').localeCompare(String(right || ''), undefined, {
      numeric: true, sensitivity: 'base',
    });
  }
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function icon(name, className) {
    const node = make('i', `fas ${name || 'fa-circle'} ${className || ''}`);
    node.setAttribute('aria-hidden', 'true');
    return node;
  }
  function setText(id, value) {
    const node = byId(id);
    if (node) node.textContent = value == null ? '' : String(value);
  }
  function stateLabel(state) {
    return {active: 'Active', inactive: 'Inactive', trash: 'Trash'}[state] || state;
  }
  function stateClasses(state) {
    return {
      active: 'bg-blue-50 text-blue-700 border-blue-100',
      inactive: 'bg-slate-100 text-slate-600 border-slate-200',
      trash: 'bg-rose-50 text-rose-700 border-rose-100',
    }[state];
  }
  function stateIcon(state) {
    return {active: 'fa-circle-check', inactive: 'fa-circle-pause', trash: 'fa-trash-can'}[state];
  }
  function toast(message, kind) {
    const region = byId('bus-toast-region');
    if (!region) return;
    const node = make('div', `${kind === 'error' ? 'bg-red-600' : 'bg-slate-900'} text-white rounded-xl shadow-xl px-4 py-3 text-sm max-w-sm`, message);
    region.append(node);
    window.setTimeout(() => node.remove(), 5000);
  }
  function openModal(id) {
    const modal = byId(id);
    if (!modal) return;
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    window.setTimeout(() => modal.querySelector('input,select,textarea,button')?.focus(), 0);
  }
  function closeModal(id) {
    byId(id)?.classList.add('hidden');
    if (!drawer || drawer.hidden) document.body.style.overflow = '';
  }

  function fillFilter(select, values, label) {
    if (!select) return;
    const current = select.value;
    select.replaceChildren(new Option(label, ''));
    Array.from(values).filter(Boolean).sort(naturalCompare).forEach((value) => {
      select.add(new Option(value, normalize(value)));
    });
    if (Array.from(select.options).some((option) => option.value === current)) select.value = current;
  }

  function populateFilters() {
    fillFilter(typeFilter, new Set(buses.map((bus) => bus.identifier)), 'All bus types');
    fillFilter(routeFilter, new Set(buses.map((bus) => bus.route)), 'All routes');
    fillFilter(schoolFilter, new Set(buses.flatMap((bus) => bus.schools || [])), 'All schools');
    fillFilter(groupFilter, new Set(buses.flatMap((bus) => bus.groups || [])), 'All groups');
  }

  function modeMatches(bus) {
    if (stateMode === 'attention') return bus.lifecycle_state === 'active' && bus.is_attention;
    if (stateMode === 'pending') return Number(bus.impact?.pending_work || 0) > 0;
    return bus.lifecycle_state === stateMode;
  }

  function searchMatches(bus, rawQuery) {
    const query = normalize(rawQuery);
    if (!query) return true;
    const haystack = normalize([
      bus.identifier, bus.name, bus.display_name, bus.route, bus.description,
      bus.status, bus.schedule_names, ...(bus.groups || []), ...(bus.schools || []),
    ].join(' '));
    return query.split(' ').every((token) => haystack.includes(token)) ||
      compact(haystack).includes(compact(query));
  }

  function filteredBuses() {
    const selectedStatus = statusFilter?.value || '';
    const selectedSchedule = normalize(scheduleFilter?.value || '');
    const selectedType = typeFilter?.value || '';
    const selectedRoute = routeFilter?.value || '';
    const selectedSchool = schoolFilter?.value || '';
    const selectedGroup = groupFilter?.value || '';
    const items = buses.filter((bus) => modeMatches(bus) && searchMatches(bus, search?.value) &&
      (!selectedStatus || bus.status === selectedStatus) &&
      (!selectedSchedule || normalize(bus.schedule_names).includes(selectedSchedule)) &&
      (!selectedType || normalize(bus.identifier) === selectedType) &&
      (!selectedRoute || normalize(bus.route) === selectedRoute) &&
      (!selectedSchool || (bus.schools || []).some((value) => normalize(value) === selectedSchool)) &&
      (!selectedGroup || (bus.groups || []).some((value) => normalize(value) === selectedGroup)));
    const sort = sortControl?.value || 'attention';
    items.sort((left, right) => {
      if (sort === 'updated') return String(right.updated_at || '').localeCompare(String(left.updated_at || '')) || naturalCompare(left.display_name, right.display_name);
      if (sort === 'route') return naturalCompare(left.route, right.route) || naturalCompare(left.display_name, right.display_name);
      if (sort === 'status') return Number(right.status_priority || 0) - Number(left.status_priority || 0) || naturalCompare(left.display_name, right.display_name);
      if (sort === 'type') return naturalCompare(left.identifier, right.identifier) || naturalCompare(left.name, right.name);
      if (sort === 'bus') return naturalCompare(left.display_name, right.display_name);
      return Number(right.is_attention) - Number(left.is_attention) || Number(right.status_priority || 0) - Number(left.status_priority || 0) || naturalCompare(left.display_name, right.display_name);
    });
    return items;
  }

  function statusBadge(bus, small) {
    const badge = make('span', `inline-flex items-center gap-1.5 rounded-full text-white font-bold ${small ? 'px-2 py-1 text-[11px]' : 'px-2.5 py-1.5 text-xs'}`);
    badge.style.backgroundColor = bus.lifecycle_state === 'active' ? bus.status_color : '#64748b';
    badge.append(icon(bus.lifecycle_state === 'active' ? bus.status_icon : stateIcon(bus.lifecycle_state), 'text-[10px]'));
    badge.append(document.createTextNode(bus.lifecycle_state === 'active' ? bus.status : stateLabel(bus.lifecycle_state)));
    return badge;
  }

  function lifecycleBadge(bus) {
    const badge = make('span', `inline-flex items-center gap-1.5 border rounded-full px-2.5 py-1 text-[11px] font-bold ${stateClasses(bus.lifecycle_state)}`);
    badge.append(icon(stateIcon(bus.lifecycle_state), 'text-[10px]'), document.createTextNode(stateLabel(bus.lifecycle_state)));
    return badge;
  }

  function actionButton(action, bus, label, iconName, classes, compactButton, displayLabel, cardButton) {
    const sizeClasses = compactButton ? 'w-9 h-9' : cardButton ? 'bus-card-action min-h-10' : 'min-h-10 px-3';
    const button = make('button', `${sizeClasses} rounded-xl text-xs font-bold inline-flex items-center justify-center gap-1.5 ${classes || 'border border-slate-200 text-slate-600 hover:bg-slate-50'}`);
    button.type = 'button';
    button.dataset.busAction = action;
    button.dataset.busId = String(bus.id);
    button.setAttribute('aria-label', `${label} ${bus.display_name}`);
    button.title = label;
    button.append(icon(iconName, 'text-xs'));
    if (!compactButton) button.append(make('span', 'bus-card-action-label', displayLabel || label));
    return button;
  }

  function busActions(bus, compactButtons, cardButtons) {
    const actions = make('div', cardButtons ? 'bus-card-actions flex items-center gap-1' : 'flex flex-wrap items-center gap-2');
    const addAction = (action, label, cardLabel, iconName, classes) => actions.append(
      actionButton(action, bus, label, iconName, classes, compactButtons, cardButtons ? cardLabel : label, cardButtons));
    addAction('details', 'Details', 'Details', 'fa-eye', '');
    if (!config.canWrite) return actions;
    if (bus.lifecycle_state !== 'trash') addAction('edit', 'Edit', 'Edit', 'fa-pen', '');
    if (bus.lifecycle_state === 'active') {
      addAction('incident', 'Status update', 'Update', 'fa-circle-plus', 'bg-blue-600 text-white hover:bg-blue-700');
      addAction('deactivate', 'Deactivate', 'Pause', 'fa-circle-pause', 'border border-amber-200 text-amber-700 hover:bg-amber-50');
      addAction('trash', 'Move to Trash', 'Trash', 'fa-trash-can', 'border border-rose-200 text-rose-700 hover:bg-rose-50');
    } else if (bus.lifecycle_state === 'inactive') {
      addAction('activate', 'Activate', 'Activate', 'fa-circle-play', 'bg-emerald-600 text-white hover:bg-emerald-700');
      addAction('trash', 'Move to Trash', 'Trash', 'fa-trash-can', 'border border-rose-200 text-rose-700 hover:bg-rose-50');
    } else {
      addAction('restore', 'Restore', 'Restore', 'fa-rotate-left', 'bg-blue-600 text-white hover:bg-blue-700');
      if (config.canPurge) {
        const purge = actionButton('purge', bus, 'Delete permanently', 'fa-trash', 'border border-red-200 text-red-700 hover:bg-red-50', compactButtons, cardButtons ? 'Delete' : 'Delete permanently', cardButtons);
        purge.disabled = !bus.purge_eligible;
        if (purge.disabled) { purge.classList.add('opacity-40', 'cursor-not-allowed'); purge.title = (bus.purge_blockers || []).join(' '); }
        actions.append(purge);
      }
    }
    return actions;
  }

  function selectionControl(bus) {
    const label = make('label', 'inline-flex items-center justify-center w-9 h-9 rounded-xl border border-slate-200 bg-white cursor-pointer flex-shrink-0');
    label.title = `Select ${bus.display_name}`;
    const checkbox = make('input', 'rounded border-slate-300 text-blue-600');
    checkbox.type = 'checkbox'; checkbox.checked = selectedBusIds.has(Number(bus.id));
    checkbox.dataset.busSelect = String(bus.id);
    checkbox.setAttribute('aria-label', `Select ${bus.display_name}`);
    label.append(checkbox);
    return label;
  }

  function createCard(bus) {
    const selected = selectedBusIds.has(Number(bus.id));
    const card = make('article', `bus-inventory-card bg-white rounded-2xl border overflow-hidden ${selected ? 'is-selected' : ''} ${bus.is_attention && bus.lifecycle_state === 'active' ? 'border-amber-200' : 'border-slate-200'}`);
    const stripe = make('div', 'h-1.5');
    stripe.style.backgroundColor = bus.lifecycle_state === 'active' ? bus.status_color : bus.lifecycle_state === 'trash' ? '#e11d48' : '#64748b';
    card.append(stripe);
    const body = make('div', 'p-4');
    const header = make('div', 'flex items-start justify-between gap-3');
    const title = make('div', 'min-w-0');
    title.append(make('h3', 'font-black text-slate-800 text-lg truncate', bus.display_name));
    title.append(make('p', 'text-xs text-slate-400 truncate mt-0.5', bus.route || 'No route specified'));
    if (selectionMode && config.canWrite) header.append(selectionControl(bus));
    header.append(title, lifecycleBadge(bus));
    body.append(header);
    const operation = make('div', 'mt-3 flex flex-wrap items-center gap-2');
    operation.append(statusBadge(bus, false));
    if (bus.delay) operation.append(make('span', 'px-2 py-1 rounded-lg bg-amber-50 text-amber-700 text-xs font-bold', `+${bus.delay} min`));
    if (bus.eta) operation.append(make('span', 'px-2 py-1 rounded-lg bg-blue-50 text-blue-700 text-xs font-bold', `ETA ${bus.eta}`));
    if (bus.impact?.pending_work) operation.append(make('span', 'px-2 py-1 rounded-lg bg-purple-50 text-purple-700 text-xs font-bold', `${bus.impact.pending_work} pending`));
    body.append(operation);
    body.append(make('p', 'text-xs text-slate-500 mt-3 truncate', bus.reason || (bus.schedule_names ? `Schedule: ${bus.schedule_names}` : 'No schedule assigned')));
    const metrics = make('div', 'grid grid-cols-3 gap-2 mt-4 pt-3 border-t border-slate-100');
    [['Groups', bus.impact?.group_assignments || 0], ['Incidents', bus.impact?.incidents || 0], ['Recipients', bus.subscriber_scope_count || 0]].forEach(([label, value]) => {
      const item = make('div', 'min-w-0'); item.append(make('p', 'font-black text-slate-700', value), make('p', 'text-[10px] text-slate-400 truncate', label)); metrics.append(item);
    });
    body.append(metrics);
    const footer = busActions(bus, false, true); footer.classList.add('mt-4'); body.append(footer);
    card.append(body);
    return card;
  }

  function createCell(className) { return make('td', `px-4 py-3 align-middle ${className || ''}`); }
  function createRow(bus) {
    const row = make('tr', `${selectedBusIds.has(Number(bus.id)) ? 'bg-blue-50/60' : 'hover:bg-slate-50/70'}`);
    if (selectionMode && config.canWrite) { const selectCell = createCell('w-12 px-3'); selectCell.append(selectionControl(bus)); row.append(selectCell); }
    const busCell = createCell(); busCell.append(make('p', 'font-black text-slate-800', bus.display_name), make('p', 'text-xs text-slate-400', bus.capacity ? `${bus.capacity} seats` : 'Capacity not set')); row.append(busCell);
    const stateCell = createCell(); stateCell.append(lifecycleBadge(bus)); row.append(stateCell);
    const statusCell = createCell(); statusCell.append(statusBadge(bus, true)); if (bus.impact?.pending_work) statusCell.append(make('p', 'mt-1 text-[11px] font-bold text-purple-600', `${bus.impact.pending_work} pending`)); row.append(statusCell);
    const routeCell = createCell(); routeCell.append(make('p', 'text-sm text-slate-600', bus.route || 'No route'), make('p', 'text-xs text-slate-400 mt-0.5', bus.schedule_names || 'No schedule')); row.append(routeCell);
    if (config.canViewNotifications) { const assignmentCell = createCell(); assignmentCell.append(make('p', 'text-sm font-bold text-slate-700', `${bus.subscriber_scope_count || 0} subscribers`), make('p', 'text-xs text-slate-400', `${bus.impact?.group_assignments || 0} groups · ${(bus.schools || []).length} schools`)); row.append(assignmentCell); }
    const updatedCell = createCell('text-xs text-slate-500'); updatedCell.textContent = bus.updated_label || bus.created_label || 'Not recorded'; row.append(updatedCell);
    const actionsCell = createCell('text-right'); const actions = busActions(bus, true); actions.classList.add('justify-end'); actionsCell.append(actions); row.append(actionsCell);
    return row;
  }

  function activeFilterCount() {
    return [search?.value, statusFilter?.value, scheduleFilter?.value, typeFilter?.value, routeFilter?.value, schoolFilter?.value, groupFilter?.value].filter(Boolean).length;
  }

  function render() {
    const items = filteredBuses();
    cardContainer?.replaceChildren(...items.map(createCard));
    listBody?.replaceChildren(...items.map(createRow));
    emptyState?.classList.toggle('hidden', items.length > 0);
    if (resultCount) resultCount.textContent = `${items.length} of ${buses.length} buses shown`;
    const filterCount = byId('bus-filter-count');
    if (filterCount) { const count = activeFilterCount(); filterCount.textContent = String(count); filterCount.classList.toggle('hidden', count === 0); filterCount.classList.toggle('inline-flex', count > 0); }
    document.querySelectorAll('[data-state-summary]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.stateSummary === stateMode)));
    byId('bus-list-select-heading')?.classList.toggle('hidden', !selectionMode);
    renderBulkBar();
    applyViewMode();
  }

  function applyViewMode() {
    const cards = viewMode === 'cards';
    cardContainer?.classList.toggle('hidden', !cards);
    listContainer?.classList.toggle('hidden', cards);
    byId('bus-card-view')?.setAttribute('aria-pressed', String(cards));
    byId('bus-list-view')?.setAttribute('aria-pressed', String(!cards));
  }

  function setViewMode(mode) {
    viewMode = mode === 'list' ? 'list' : 'cards';
    try { localStorage.setItem(viewStorageKey, viewMode); } catch (_error) { /* no-op */ }
    applyViewMode();
  }

  function setStateMode(mode) {
    if (!validStates.has(mode)) return;
    stateMode = mode; selectedBusIds.clear();
    const url = new URL(window.location.href); url.searchParams.set('state', mode); history.replaceState({}, '', url);
    closeDrawer(); render();
  }

  function selectedBuses() {
    return Array.from(selectedBusIds).map((id) => busById.get(Number(id))).filter(Boolean);
  }

  function renderBulkBar() {
    if (!bulkBar || !config.canWrite) return;
    bulkBar.classList.toggle('hidden', !selectionMode);
    setText('bus-selected-count', selectedBusIds.size);
    const review = byId('bus-review-selection'); if (review) review.disabled = selectedBusIds.size === 0;
    const toggle = byId('bus-selection-toggle'); if (toggle) toggle.setAttribute('aria-pressed', String(selectionMode));
    setText('bus-selection-toggle-label', selectionMode ? 'Done selecting' : 'Select buses');
  }

  function setSelectionMode(enabled) {
    selectionMode = Boolean(enabled);
    if (!selectionMode) selectedBusIds.clear();
    render();
  }

  function availableBulkActions(items) {
    const states = new Set(items.map((bus) => bus.lifecycle_state));
    if (states.size === 1 && states.has('active')) return [['deactivate', 'Deactivate'], ['trash', 'Move to Trash']];
    if (states.size === 1 && states.has('inactive')) return [['activate', 'Activate'], ['trash', 'Move to Trash']];
    if (states.size === 1 && states.has('trash')) return [['restore', 'Restore as inactive']];
    if (states.size === 2 && states.has('active') && states.has('inactive')) return [['trash', 'Move to Trash']];
    return [];
  }

  function combinedImpact(items) {
    const keys = ['incidents', 'group_assignments', 'direct_assignments', 'notification_logs', 'outbox_messages', 'pending_work', 'external_identities', 'import_changes'];
    return Object.fromEntries(keys.map((key) => [key, items.reduce((sum, bus) => sum + Number(bus.impact?.[key] || 0), 0)]));
  }

  function renderBulkReview() {
    const items = selectedBuses(); const action = byId('bus-bulk-action')?.value || '';
    const impact = combinedImpact(items); const impactTarget = byId('bus-bulk-impact');
    if (impactTarget) { impactTarget.replaceChildren(); [['Incidents', 'incidents'], ['Groups', 'group_assignments'], ['Direct links', 'direct_assignments'], ['Delivery logs', 'notification_logs'], ['Outbox', 'outbox_messages'], ['Pending', 'pending_work'], ['PS identities', 'external_identities'], ['Import changes', 'import_changes']].forEach(([label, key]) => { const card = make('div', 'rounded-xl border border-slate-100 bg-slate-50 p-2.5'); card.append(make('p', 'text-lg font-black text-slate-800', impact[key]), make('p', 'text-[10px] text-slate-500', label)); impactTarget.append(card); }); }
    const list = byId('bus-bulk-list'); if (list) { list.replaceChildren(...items.map((bus) => { const row = make('div', 'px-3 py-2.5 flex items-center justify-between gap-3'); row.append(make('span', 'font-bold text-slate-700', bus.display_name), lifecycleBadge(bus)); return row; })); }
    const pending = items.filter((bus) => Number(bus.impact?.pending_work || 0) > 0);
    const warnings = [];
    if (!action) warnings.push('The selected lifecycle states do not share a safe bulk action.');
    if (pending.length && ['deactivate', 'trash'].includes(action)) warnings.push(`${pending.length} selected bus(es) have pending work and will block the entire operation.`);
    const warning = byId('bus-bulk-warning'); if (warning) { warning.textContent = warnings.join(' '); warning.classList.toggle('hidden', warnings.length === 0); }
    byId('bus-bulk-reason-wrap')?.classList.toggle('hidden', !['deactivate', 'trash'].includes(action));
    const submit = byId('bus-bulk-submit'); if (submit) submit.disabled = warnings.length > 0;
  }

  function openBulkModal() {
    const items = selectedBuses(); if (!items.length || !bulkModal) return;
    setText('bus-bulk-subtitle', `${items.length} bus${items.length === 1 ? '' : 'es'} selected`);
    const actionSelect = byId('bus-bulk-action'); const actions = availableBulkActions(items);
    if (actionSelect) { actionSelect.replaceChildren(...actions.map(([value, label]) => new Option(label, value))); if (!actions.length) actionSelect.add(new Option('No compatible bulk action', '')); }
    byId('bus-bulk-reason').value = ''; byId('bus-bulk-confirm').checked = false;
    renderBulkReview(); openModal('bus-bulk-modal');
  }

  function closeBulkModal() { closeModal('bus-bulk-modal'); }

  async function submitBulkLifecycle(event) {
    event.preventDefault();
    const items = selectedBuses(); const action = byId('bus-bulk-action')?.value || ''; const reason = byId('bus-bulk-reason')?.value.trim() || '';
    if (!byId('bus-bulk-confirm')?.checked) return toast('Confirm that you reviewed the selected buses.', 'error');
    if (['deactivate', 'trash'].includes(action) && !reason) return toast('Enter a reason for this bulk change.', 'error');
    const submit = byId('bus-bulk-submit'); if (submit) { submit.disabled = true; submit.textContent = 'Applying…'; }
    try {
      const response = await fetch('/admin/buses/bulk-lifecycle', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': config.csrfToken}, body: JSON.stringify({bus_ids: items.map((bus) => bus.id), action, reason, confirmed: true, expected_versions: Object.fromEntries(items.map((bus) => [String(bus.id), bus.lifecycle_version]))})});
      const data = await response.json();
      if (!response.ok || !data.ok) { const details = Array.isArray(data.blockers) && data.blockers.length ? ` ${data.blockers.join(' ')}` : ''; throw new Error((data.message || 'The bulk operation could not be completed.') + details); }
      const url = new URL(window.location.href); url.searchParams.set('state', data.destination_state || 'active'); window.location.assign(url.pathname + url.search);
    } catch (error) {
      const warning = byId('bus-bulk-warning'); if (warning) { warning.textContent = error.message || 'The bulk operation could not be completed.'; warning.classList.remove('hidden'); }
      if (submit) { submit.disabled = false; submit.textContent = 'Apply to selected buses'; }
    }
  }

  function impactCards(bus, target) {
    if (!target) return;
    target.replaceChildren();
    const values = [
      ['Incidents', bus.impact?.incidents || 0], ['Groups', bus.impact?.group_assignments || 0],
      ['Direct links', bus.impact?.direct_assignments || 0], ['Delivery logs', bus.impact?.notification_logs || 0],
      ['Outbox', bus.impact?.outbox_messages || 0], ['Pending', bus.impact?.pending_work || 0],
      ['PS identities', bus.impact?.external_identities || 0], ['Import changes', bus.impact?.import_changes || 0],
    ];
    values.forEach(([label, value]) => { const card = make('div', 'rounded-xl border border-slate-100 bg-slate-50/70 p-2.5'); card.append(make('p', 'text-lg font-black text-slate-800', value), make('p', 'text-[10px] text-slate-500', label)); target.append(card); });
  }

  function metadataRow(label, value) {
    const row = make('div', 'px-3 py-2.5 grid grid-cols-[7rem_1fr] gap-3'); row.append(make('dt', 'text-xs font-bold text-slate-400', label), make('dd', 'text-sm text-slate-600 break-words', value || 'Not recorded')); return row;
  }

  function openDrawer(bus, trigger) {
    if (!drawer || !drawerOverlay) return;
    drawerBusId = Number(bus.id); drawerReturnFocus = trigger || document.activeElement;
    setText('bus-drawer-state', stateLabel(bus.lifecycle_state)); setText('bus-drawer-title', bus.display_name); setText('bus-drawer-route', bus.route || 'No route specified');
    const status = byId('bus-drawer-status'); if (status) { status.replaceChildren(icon(bus.lifecycle_state === 'active' ? bus.status_icon : stateIcon(bus.lifecycle_state), 'text-xs'), document.createTextNode(bus.lifecycle_state === 'active' ? bus.status : stateLabel(bus.lifecycle_state))); status.style.backgroundColor = bus.lifecycle_state === 'active' ? bus.status_color : '#64748b'; }
    setText('bus-drawer-delay', bus.delay ? `+${bus.delay} min${bus.eta ? ` · ETA ${bus.eta}` : ''}` : (bus.eta ? `ETA ${bus.eta}` : ''));
    setText('bus-drawer-reason', bus.reason || (bus.lifecycle_state === 'active' ? 'No current service exception.' : 'Operational updates are disabled for this bus.'));
    setText('bus-drawer-schedules', bus.schedules?.length ? bus.schedules.map((item) => `${item.name}${item.departure_label ? ` ${item.departure_label}` : ''}${item.warning ? ' · check time' : ''}`).join(', ') : 'No schedule assigned');
    impactCards(bus, byId('bus-drawer-impact'));
    if (config.canViewNotifications) { setText('bus-drawer-subscribers', bus.subscriber_scope_count || 0); setText('bus-drawer-groups', `Groups: ${(bus.groups || []).join(', ') || 'none'}`); setText('bus-drawer-schools', `Schools: ${(bus.schools || []).join(', ') || 'none'}`); }
    const metadata = byId('bus-drawer-metadata'); if (metadata) { metadata.replaceChildren(metadataRow('Created', bus.created_label), metadataRow('Updated', bus.updated_label), metadataRow('Deactivated', bus.deactivated_label ? `${bus.deactivated_label}${bus.deactivated_by ? ` by ${bus.deactivated_by}` : ''}` : ''), metadataRow('Trash', bus.deleted_label ? `${bus.deleted_label}${bus.deleted_by ? ` by ${bus.deleted_by}` : ''}` : ''), metadataRow('Reason', bus.deletion_reason || bus.deactivation_reason)); }
    setText('bus-drawer-description', bus.description || 'No description or internal notes.');
    const alert = byId('bus-drawer-alert'); if (alert) { const pending = Number(bus.impact?.pending_work || 0); const blockers = bus.lifecycle_state === 'trash' ? (bus.purge_blockers || []) : []; const messages = []; if (pending) messages.push(`${pending} pending operation(s) must be resolved before deactivation or Trash.`); messages.push(...blockers); alert.textContent = messages.join(' '); alert.className = `rounded-xl border p-3 text-sm ${messages.length ? 'border-amber-200 bg-amber-50 text-amber-800' : 'hidden'}`; }
    const actions = byId('bus-drawer-actions'); if (actions) actions.replaceChildren(...Array.from(busActions(bus, false).children));
    drawer.hidden = false; drawerOverlay.hidden = false; drawer.setAttribute('aria-hidden', 'false'); drawerOverlay.setAttribute('aria-hidden', 'false'); document.body.style.overflow = 'hidden';
    window.requestAnimationFrame(() => { drawer.classList.add('is-open'); drawerOverlay.classList.add('is-open'); drawer.querySelector('[data-close-bus-drawer]')?.focus(); });
  }

  function closeDrawer() {
    if (!drawer || !drawerOverlay || drawer.hidden) return;
    drawerBusId = null; drawer.classList.remove('is-open'); drawerOverlay.classList.remove('is-open'); drawer.setAttribute('aria-hidden', 'true'); drawerOverlay.setAttribute('aria-hidden', 'true'); document.body.style.overflow = '';
    window.setTimeout(() => { drawer.hidden = true; drawerOverlay.hidden = true; }, 220); drawerReturnFocus?.focus?.();
  }

  function openEdit(bus) {
    if (bus.lifecycle_state === 'trash') return toast('Restore this bus before editing it.', 'error');
    const form = byId('edit-bus-form'); if (!form) return;
    form.action = `/admin/buses/${bus.id}/edit`;
    const next = byId('edit-bus-next'); if (next) next.value = window.location.pathname + window.location.search;
    byId('edit-bus-identifier').value = bus.identifier || ''; byId('edit-bus-name').value = bus.name || ''; byId('edit-bus-route').value = bus.route || ''; byId('edit-bus-capacity').value = bus.capacity || ''; byId('edit-bus-description').value = bus.description || '';
    const assignments = new Map((bus.schedules || []).map((item) => [Number(item.id), item]));
    document.querySelectorAll('[data-edit-schedule]').forEach((checkbox) => { const id = Number(checkbox.dataset.editSchedule); const assignment = assignments.get(id); checkbox.checked = Boolean(assignment); byId(`edit-schedule-time-${id}`)?.classList.toggle('hidden', !assignment); const input = byId(`edit-schedule-input-${id}`); if (input) input.value = assignment?.departure_time || ''; });
    closeDrawer(); openModal('modal-edit-bus');
  }

  function openIncident(bus) {
    if (bus.lifecycle_state !== 'active') return toast('Only active buses can receive status updates.', 'error');
    const form = byId('bus-incident-form'); if (!form) return;
    form.action = `/admin/buses/${bus.id}/incident`; setText('incident-bus-label', bus.display_name);
    form.querySelectorAll('input[type="radio"]').forEach((input) => { input.checked = false; });
    const resetValues = {'inc-delay': '0', 'inc-eta': '', 'inc-reason-select': '', 'inc-reason-text': '', 'incident-notes': '', 'incident-expected-latest': '', 'incident-replace-id': ''}; Object.entries(resetValues).forEach(([id, value]) => { if (byId(id)) byId(id).value = value; });
    if (byId('incident-schedule')) byId('incident-schedule').value = config.currentPeriodId ? String(config.currentPeriodId) : '';
    const next = byId('incident-next'); if (next) next.value = window.location.pathname + window.location.search;
    byId('inc-reason-custom')?.classList.add('hidden'); byId('add-reason-panel')?.classList.add('hidden'); closeDrawer(); openModal('modal-incident');
  }

  const lifecycleCopy = {
    deactivate: ['Deactivate bus', 'Remove this bus from public and operational views. Its history and routing assignments remain intact.', 'Deactivate', 'bg-amber-600'],
    activate: ['Activate bus', 'Return this bus to live operations, the dashboard and public portal.', 'Activate', 'bg-emerald-600'],
    trash: ['Move bus to Trash', 'Hide this bus from normal inventory views while preserving a recoverable record and all history.', 'Move to Trash', 'bg-rose-600'],
    restore: ['Restore bus', 'Restore this record as inactive so it can be reviewed before returning to service.', 'Restore as inactive', 'bg-blue-600'],
    purge: ['Delete bus permanently', 'This irreversible action is allowed only for an unused bus after the retention period.', 'Delete permanently', 'bg-red-700'],
  };

  function openLifecycle(action, bus) {
    const modal = byId('bus-lifecycle-modal'); const form = byId('bus-lifecycle-form'); if (!modal || !form) return;
    lifecycleContext = {action, bus}; const [title, copy, submitLabel, submitClass] = lifecycleCopy[action];
    setText('bus-lifecycle-eyebrow', bus.display_name); setText('bus-lifecycle-title', title); setText('bus-lifecycle-copy', copy); setText('bus-lifecycle-submit', submitLabel);
    form.action = `/admin/buses/${bus.id}/${action === 'deactivate' ? 'deactivate' : action}`;
    const destinationState = {deactivate: 'inactive', activate: 'active', trash: 'trash', restore: 'inactive', purge: 'trash'}[action] || stateMode;
    const nextUrl = new URL(window.location.href); nextUrl.searchParams.set('state', destinationState);
    byId('bus-lifecycle-next').value = nextUrl.pathname + nextUrl.search;
    byId('bus-lifecycle-reason').value = ''; byId('bus-purge-confirm').value = '';
    const reasonWrap = byId('bus-lifecycle-reason-wrap'); if (reasonWrap) reasonWrap.classList.toggle('hidden', !['deactivate', 'trash'].includes(action));
    const purgeWrap = byId('bus-purge-confirm-wrap'); if (purgeWrap) purgeWrap.classList.toggle('hidden', action !== 'purge');
    impactCards(bus, byId('bus-lifecycle-impact'));
    const warnings = []; const pending = Number(bus.impact?.pending_work || 0); if (['deactivate', 'trash'].includes(action) && pending) warnings.push(`${pending} pending operation(s) must be resolved first.`); if (action === 'purge') warnings.push(...(bus.purge_blockers || []));
    const warning = byId('bus-lifecycle-warning'); if (warning) { warning.textContent = warnings.join(' '); warning.classList.toggle('hidden', warnings.length === 0); }
    const submit = byId('bus-lifecycle-submit'); if (submit) { submit.className = `min-h-11 px-4 rounded-xl text-white font-black ${submitClass}`; submit.disabled = warnings.length > 0; submit.classList.toggle('opacity-40', submit.disabled); }
    openModal('bus-lifecycle-modal');
  }

  function closeLifecycle() { lifecycleContext = null; closeModal('bus-lifecycle-modal'); }

  function handleAction(button) {
    const bus = busById.get(Number(button.dataset.busId)); if (!bus) return;
    const action = button.dataset.busAction;
    if (action === 'details') return openDrawer(bus, button);
    if (action === 'edit') return openEdit(bus);
    if (action === 'incident') return openIncident(bus);
    openLifecycle(action, bus);
  }

  [search, statusFilter, scheduleFilter, typeFilter, routeFilter, schoolFilter, groupFilter].filter(Boolean).forEach((control) => control.addEventListener(control.tagName === 'INPUT' ? 'input' : 'change', render));
  sortControl?.addEventListener('change', render);
  document.querySelectorAll('[data-state-summary]').forEach((button) => button.addEventListener('click', () => setStateMode(button.dataset.stateSummary)));
  byId('bus-card-view')?.addEventListener('click', () => setViewMode('cards')); byId('bus-list-view')?.addEventListener('click', () => setViewMode('list'));
  byId('bus-selection-toggle')?.addEventListener('click', () => setSelectionMode(!selectionMode));
  byId('bus-select-visible')?.addEventListener('click', () => { const visible = filteredBuses().slice(0, 250); visible.forEach((bus) => selectedBusIds.add(Number(bus.id))); if (filteredBuses().length > 250) toast('Only the first 250 visible buses were selected.', 'error'); render(); });
  byId('bus-clear-selection')?.addEventListener('click', () => { selectedBusIds.clear(); render(); });
  byId('bus-review-selection')?.addEventListener('click', openBulkModal);
  byId('bus-bulk-action')?.addEventListener('change', renderBulkReview);
  byId('bus-bulk-form')?.addEventListener('submit', submitBulkLifecycle);
  document.querySelectorAll('[data-close-bulk]').forEach((button) => button.addEventListener('click', closeBulkModal));
  byId('bus-clear-filters')?.addEventListener('click', () => { [search, statusFilter, scheduleFilter, typeFilter, routeFilter, schoolFilter, groupFilter].filter(Boolean).forEach((control) => { control.value = ''; }); if (sortControl) sortControl.value = 'attention'; render(); });
  byId('bus-filter-toggle')?.addEventListener('click', (event) => { const open = event.currentTarget.getAttribute('aria-expanded') !== 'true'; event.currentTarget.setAttribute('aria-expanded', String(open)); byId('bus-filter-panel')?.classList.toggle('is-open', open); });
  byId('open-add-bus')?.addEventListener('click', () => openModal('modal-add-bus'));
  document.addEventListener('change', (event) => { const checkbox = event.target.closest('[data-bus-select]'); if (!checkbox) return; const busId = Number(checkbox.dataset.busSelect); if (checkbox.checked) selectedBusIds.add(busId); else selectedBusIds.delete(busId); render(); });
  document.addEventListener('click', (event) => { const action = event.target.closest('[data-bus-action]'); if (action) handleAction(action); });
  document.querySelectorAll('[data-close-modal]').forEach((button) => button.addEventListener('click', () => closeModal(button.dataset.closeModal)));
  document.querySelectorAll('[data-close-lifecycle]').forEach((button) => button.addEventListener('click', closeLifecycle));
  document.querySelectorAll('[data-close-bus-drawer]').forEach((button) => button.addEventListener('click', closeDrawer)); drawerOverlay?.addEventListener('click', closeDrawer);
  document.querySelectorAll('[data-add-schedule]').forEach((checkbox) => checkbox.addEventListener('change', () => byId(`add-schedule-time-${checkbox.dataset.addSchedule}`)?.classList.toggle('hidden', !checkbox.checked)));
  document.querySelectorAll('[data-edit-schedule]').forEach((checkbox) => checkbox.addEventListener('change', () => byId(`edit-schedule-time-${checkbox.dataset.editSchedule}`)?.classList.toggle('hidden', !checkbox.checked)));
  document.querySelectorAll('[data-close-incident]').forEach((button) => button.addEventListener('click', () => closeModal('modal-incident')));
  byId('bus-lifecycle-form')?.addEventListener('submit', (event) => { if (!lifecycleContext) return event.preventDefault(); if (lifecycleContext.action === 'trash' && !byId('bus-lifecycle-reason').value.trim()) { event.preventDefault(); return toast('Enter a reason before moving this bus to Trash.', 'error'); } if (lifecycleContext.action === 'purge' && byId('bus-purge-confirm').value.trim() !== lifecycleContext.bus.display_name) { event.preventDefault(); return toast(`Type ${lifecycleContext.bus.display_name} exactly to confirm.`, 'error'); } const submit = byId('bus-lifecycle-submit'); if (submit) { submit.disabled = true; submit.textContent = 'Working…'; } });
  document.addEventListener('keydown', (event) => { if (event.key !== 'Escape') return; if (!byId('bus-bulk-modal')?.classList.contains('hidden')) closeBulkModal(); else if (!byId('bus-lifecycle-modal')?.classList.contains('hidden')) closeLifecycle(); else if (!byId('modal-add-bus')?.classList.contains('hidden')) closeModal('modal-add-bus'); else if (!byId('modal-edit-bus')?.classList.contains('hidden')) closeModal('modal-edit-bus'); else if (!byId('modal-incident')?.classList.contains('hidden')) closeModal('modal-incident'); else closeDrawer(); });

  window.toggleCustomReason = function () { const select = byId('inc-reason-select'); byId('inc-reason-custom')?.classList.toggle('hidden', select?.value !== 'custom'); if (select?.value !== 'custom' && byId('inc-reason-text')) byId('inc-reason-text').value = ''; };
  window.showAddReason = function () { byId('add-reason-panel')?.classList.remove('hidden'); byId('new-reason-input')?.focus(); };
  window.submitNewReason = function () { const input = byId('new-reason-input'); if (!input?.value.trim()) return; fetch('/admin/delay-reasons/add', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF-Token': config.csrfToken}, body: `reason=${encodeURIComponent(input.value.trim())}`}).then((response) => response.json()).then((data) => { if (!data.success) return; const select = byId('inc-reason-select'); const custom = select?.querySelector('option[value="custom"]'); if (!select || !custom) return; select.insertBefore(new Option(data.reason, String(data.id)), custom); select.value = String(data.id); input.value = ''; byId('add-reason-panel')?.classList.add('hidden'); }).catch(() => toast('Could not add the delay reason.', 'error')); };

  populateFilters(); render();
})();
