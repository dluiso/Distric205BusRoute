(function () {
  'use strict';

  const configElement = document.getElementById('dashboard-config');
  if (!configElement) return;
  const config = JSON.parse(configElement.textContent || '{}');
  const busById = new Map();
  let pendingQueue = Array.isArray(config.pendingQueue) ? config.pendingQueue : [];
  let operationsRevision = config.operationsRevision || '';
  let summaryMode = 'all';
  let drawerReturnFocus = null;
  let openBusId = null;
  let bulkMode = false;
  let polling = false;
  const selectedBusIds = new Set();
  const changedVersions = new Set();

  const byId = (id) => document.getElementById(id);
  const search = byId('dashboard-bus-search');
  const statusFilter = byId('dashboard-status-filter');
  const scheduleFilter = byId('dashboard-schedule-filter');
  const routeFilter = byId('dashboard-route-filter');
  const groupFilter = byId('dashboard-group-filter');
  const schoolFilter = byId('dashboard-school-filter');
  const savedView = byId('dashboard-saved-view');
  const filterToggle = byId('dashboard-filter-toggle');
  const filterPanel = byId('dashboard-filter-panel');
  const resultStatus = byId('dashboard-filter-status');
  const noResults = byId('dashboard-no-results');
  const attentionSection = byId('attention-bus-section');
  const onTimeSection = byId('on-time-bus-section');
  const attentionGrid = byId('attention-bus-grid');
  const onTimeGrid = byId('on-time-bus-grid');
  const allClear = byId('all-clear-state');
  const drawer = byId('dashboard-bus-drawer');
  const overlay = byId('dashboard-drawer-overlay');
  const drawerForm = byId('dashboard-incident-form');
  const drawerToggleForm = byId('drawer-toggle-form');

  function normalize(value) {
    return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }

  function naturalCompare(left, right) {
    return String(left || '').localeCompare(String(right || ''), undefined, {numeric: true, sensitivity: 'base'});
  }

  function setText(id, value) {
    const element = byId(id);
    if (element) element.textContent = value == null ? '' : String(value);
  }

  function makeElement(tag, className, textValue) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (textValue != null) element.textContent = textValue;
    return element;
  }

  function toast(message, kind) {
    const region = byId('dashboard-toast-region');
    if (!region) return;
    const item = makeElement('div', `${kind === 'error' ? 'bg-red-600' : kind === 'warning' ? 'bg-amber-500' : 'bg-slate-900'} text-white rounded-xl shadow-xl px-4 py-3 text-sm max-w-sm`, message);
    region.append(item);
    window.setTimeout(() => item.remove(), 5000);
  }

  function setBuses(buses) {
    busById.clear();
    (buses || []).forEach((bus) => busById.set(Number(bus.id), bus));
    Array.from(selectedBusIds).forEach((id) => {
      if (!busById.has(id)) selectedBusIds.delete(id);
    });
  }

  function createBusCard(bus, compact) {
    const card = makeElement('article', `dashboard-bus-card dashboard-filterable relative bg-white rounded-2xl border overflow-hidden ${bus.is_attention ? 'border-amber-200' : 'border-slate-100'}`);
    Object.assign(card.dataset, {
      busId: String(bus.id), status: bus.status || '', schedule: normalize(bus.schedule_names),
      route: normalize(bus.route), groups: normalize((bus.group_names || []).join(' ')),
      schools: normalize((bus.school_names || []).join(' ')), version: bus.version || '0',
      search: normalize(`${bus.identifier} ${bus.name} ${bus.route || ''}`),
      attention: bus.is_attention ? '1' : '0', pending: bus.latest && bus.latest.pending ? '1' : '0',
    });
    if (changedVersions.has(Number(bus.id))) card.classList.add('is-changed');
    if (selectedBusIds.has(Number(bus.id))) card.classList.add('is-selected');

    if (config.canWrite) {
      const selector = makeElement('label', `dashboard-bulk-selector ${bulkMode ? 'flex' : 'hidden'} absolute z-10 mt-3 ml-3 w-9 h-9 rounded-xl bg-white border border-slate-200 shadow-sm items-center justify-center`);
      selector.setAttribute('aria-label', `Select ${bus.display_name}`);
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.dataset.selectBus = String(bus.id);
      checkbox.className = 'w-4 h-4 rounded border-slate-300 text-blue-600';
      checkbox.checked = selectedBusIds.has(Number(bus.id));
      selector.append(checkbox);
      card.append(selector);
    }

    const button = makeElement('button', `w-full text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-500 ${bulkMode ? 'pl-10' : ''}`);
    button.type = 'button';
    button.dataset.openBus = String(bus.id);
    const stripe = makeElement('div', 'h-1.5');
    stripe.style.background = bus.status_color;
    button.append(stripe);
    const body = makeElement('div', compact ? 'p-3' : 'p-4');
    const header = makeElement('div', 'flex items-start justify-between gap-3');
    const names = makeElement('div', 'min-w-0');
    names.append(makeElement('p', `font-black text-slate-800 ${compact ? 'text-sm' : 'text-base'} truncate`, bus.display_name));
    names.append(makeElement('p', 'text-xs text-slate-400 truncate mt-0.5', bus.route || 'No route specified'));
    const badge = makeElement('span', 'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-white text-xs font-semibold flex-shrink-0');
    badge.style.background = bus.status_color;
    const icon = makeElement('i', `fas ${bus.status_icon}`);
    icon.setAttribute('aria-hidden', 'true');
    badge.append(icon, document.createTextNode(bus.status));
    header.append(names, badge);
    body.append(header);
    if (bus.is_attention) {
      const chips = makeElement('div', 'flex flex-wrap gap-2 mt-3 text-xs');
      if (bus.delay) chips.append(makeElement('span', 'px-2 py-1 rounded-lg bg-amber-50 text-amber-700 font-semibold', `+${bus.delay} min`));
      if (bus.eta_label) chips.append(makeElement('span', 'px-2 py-1 rounded-lg bg-blue-50 text-blue-700 font-semibold', `ETA ${bus.eta_label}`));
      if (bus.latest && bus.latest.pending) {
        const pending = makeElement('span', 'px-2 py-1 rounded-lg bg-purple-50 text-purple-700 font-semibold dashboard-countdown');
        pending.dataset.pendingUntil = bus.latest.pending_until_utc || '';
        chips.append(pending);
      }
      body.append(chips);
      if (bus.reason) body.append(makeElement('p', 'text-xs text-slate-500 mt-2 truncate', bus.reason));
    }
    if (!compact) {
      const footer = makeElement('div', 'flex items-center justify-between mt-3 pt-3 border-t border-slate-100 text-xs text-slate-400');
      footer.append(makeElement('span', '', bus.schedule_names || 'No schedule'), makeElement('span', 'text-blue-600 font-semibold', 'Open ›'));
      body.append(footer);
    }
    button.append(body);
    card.append(button);
    return card;
  }

  function renderBusCards() {
    if (!attentionGrid || !onTimeGrid) return;
    attentionGrid.replaceChildren();
    onTimeGrid.replaceChildren();
    const buses = Array.from(busById.values());
    buses.filter((bus) => bus.is_attention).forEach((bus) => attentionGrid.append(createBusCard(bus, false)));
    buses.filter((bus) => !bus.is_attention).forEach((bus) => onTimeGrid.append(createBusCard(bus, true)));
    allClear?.classList.toggle('hidden', buses.some((bus) => bus.is_attention));
    populateDynamicFilters();
    applyFilters();
    updateSelectionUI();
    updateCountdowns();
  }

  function optionValues(kind) {
    const values = new Set();
    busById.forEach((bus) => {
      if (kind === 'route' && bus.route) values.add(bus.route);
      if (kind === 'group') (bus.group_names || []).forEach((value) => values.add(value));
      if (kind === 'school') (bus.school_names || []).forEach((value) => values.add(value));
    });
    return Array.from(values).sort(naturalCompare);
  }

  function fillFilter(select, kind, label) {
    if (!select) return;
    const current = select.value;
    select.replaceChildren(new Option(label, ''));
    optionValues(kind).forEach((value) => select.add(new Option(value, normalize(value))));
    select.value = Array.from(select.options).some((option) => option.value === current) ? current : '';
  }

  function populateDynamicFilters() {
    fillFilter(routeFilter, 'route', 'Route');
    fillFilter(groupFilter, 'group', 'Group');
    fillFilter(schoolFilter, 'school', 'School');
  }

  function matchesSearch(card, query) {
    if (!query) return true;
    const haystack = normalize(card.dataset.search);
    return query.split(' ').every((token) => haystack.includes(token)) || haystack.replace(/\s+/g, '').includes(query.replace(/\s+/g, ''));
  }

  function applyFilters() {
    const query = normalize(search?.value);
    const selectedStatus = statusFilter?.value || '';
    const selectedSchedule = normalize(scheduleFilter?.value);
    const selectedRoute = routeFilter?.value || '';
    const selectedGroup = groupFilter?.value || '';
    const selectedSchool = schoolFilter?.value || '';
    const cards = Array.from(document.querySelectorAll('.dashboard-filterable'));
    let visible = 0;
    let visibleAttention = 0;
    let visibleOnTime = 0;
    cards.forEach((card) => {
      const attention = card.dataset.attention === '1';
      const pending = card.dataset.pending === '1';
      const modeMatches = summaryMode === 'all' || (summaryMode === 'attention' && attention) || (summaryMode === 'pending' && pending) || (summaryMode === 'on-time' && !attention);
      const matches = modeMatches && matchesSearch(card, query) &&
        (!selectedStatus || card.dataset.status === selectedStatus) &&
        (!selectedSchedule || card.dataset.schedule.includes(selectedSchedule)) &&
        (!selectedRoute || card.dataset.route === selectedRoute) &&
        (!selectedGroup || card.dataset.groups.includes(selectedGroup)) &&
        (!selectedSchool || card.dataset.schools.includes(selectedSchool));
      card.hidden = !matches;
      card.classList.toggle('hidden', !matches);
      if (matches) { visible += 1; if (attention) visibleAttention += 1; else visibleOnTime += 1; }
    });
    attentionSection?.classList.toggle('hidden', visibleAttention === 0 && summaryMode !== 'all');
    if (attentionSection && summaryMode === 'all') attentionSection.classList.remove('hidden');
    if (onTimeSection) {
      onTimeSection.classList.toggle('hidden', visibleOnTime === 0);
      if (query || selectedStatus || selectedSchedule || selectedRoute || selectedGroup || selectedSchool || summaryMode === 'on-time') onTimeSection.open = visibleOnTime > 0;
    }
    noResults?.classList.toggle('hidden', visible !== 0);
    if (resultStatus) resultStatus.textContent = `${visible}/${cards.length} buses`;
    const activeFilterCount = [query, selectedStatus, selectedSchedule, selectedRoute, selectedGroup, selectedSchool].filter(Boolean).length + (summaryMode === 'all' ? 0 : 1);
    const filterCount = byId('dashboard-filter-count');
    if (filterCount) {
      filterCount.textContent = String(activeFilterCount);
      filterCount.classList.toggle('hidden', activeFilterCount === 0);
      filterCount.classList.toggle('inline-flex', activeFilterCount > 0);
    }
    document.querySelectorAll('[data-summary-filter]').forEach((button) => {
      const active = button.dataset.summaryFilter === summaryMode;
      button.setAttribute('aria-pressed', String(active));
      button.classList.toggle('ring-2', active);
      button.classList.toggle('ring-blue-400', active);
    });
  }

  function currentFilters() {
    return {search: search?.value || '', status: statusFilter?.value || '', schedule: scheduleFilter?.value || '', route: routeFilter?.value || '', group: groupFilter?.value || '', school: schoolFilter?.value || '', summaryMode};
  }

  function applySavedFilters(values) {
    if (!values) return;
    if (search) search.value = values.search || '';
    if (statusFilter) statusFilter.value = values.status || '';
    if (scheduleFilter) scheduleFilter.value = values.schedule || '';
    if (routeFilter) routeFilter.value = values.route || '';
    if (groupFilter) groupFilter.value = values.group || '';
    if (schoolFilter) schoolFilter.value = values.school || '';
    summaryMode = values.summaryMode || 'all';
    applyFilters();
  }

  function storedViews() {
    try { return JSON.parse(localStorage.getItem(config.savedViewsKey) || '[]'); } catch (_error) { return []; }
  }

  function renderSavedViews() {
    if (!savedView) return;
    savedView.replaceChildren(new Option('Views…', ''));
    storedViews().forEach((view, index) => savedView.add(new Option(view.name, String(index))));
  }

  function saveView() {
    const name = window.prompt('Name this dashboard view:');
    if (!name || !name.trim()) return;
    const views = storedViews();
    const item = {name: name.trim().slice(0, 60), filters: currentFilters()};
    const existing = views.findIndex((view) => view.name.toLowerCase() === item.name.toLowerCase());
    if (existing >= 0) views[existing] = item; else views.push(item);
    localStorage.setItem(config.savedViewsKey, JSON.stringify(views.slice(-20)));
    renderSavedViews();
    toast('Dashboard view saved.');
  }

  function updateKpis(payload) {
    const values = {attention: payload.attention_count, pending: payload.pending_count, 'on-time': payload.on_time_count, total: payload.total_buses};
    Object.entries(values).forEach(([key, value]) => document.querySelectorAll(`[data-kpi="${key}"]`).forEach((node) => { node.textContent = String(value || 0); }));
    setText('dashboard-pending-badge', payload.pending_count || 0);
    setText('dashboard-pending-count', (payload.pending_queue || []).length);
    setText('dashboard-period-name', payload.current_period_name ? `${payload.current_period_name} period` : 'No active period');
  }

  function updateSyncStatus(label, error) {
    const status = byId('dashboard-sync-status');
    if (!status) return;
    status.classList.toggle('bg-red-50', Boolean(error));
    status.classList.toggle('text-red-700', Boolean(error));
    const icon = status.querySelector('i');
    if (icon) icon.className = `fas ${error ? 'fa-triangle-exclamation text-red-500' : 'fa-signal text-emerald-500'}`;
    const span = status.querySelector('span');
    if (span) span.textContent = error ? 'Live refresh unavailable' : `Updated ${label}`;
  }

  async function refreshOperations(manual) {
    if (!config.operationsUrl || polling || (document.hidden && !manual)) return;
    polling = true;
    const button = byId('dashboard-refresh');
    button?.querySelector('i')?.classList.add('fa-spin');
    try {
      const headers = {};
      if (operationsRevision) headers['If-None-Match'] = `"${operationsRevision}"`;
      const response = await fetch(config.operationsUrl, {headers, credentials: 'same-origin'});
      if (response.status === 304) {
        updateSyncStatus(new Date().toLocaleTimeString([], {hour: 'numeric', minute: '2-digit', second: '2-digit'}), false);
        if (manual) toast('Dashboard is already current.');
        return;
      }
      if (!response.ok) throw new Error('Live dashboard request failed');
      const payload = await response.json();
      const previous = new Map(Array.from(busById.values()).map((bus) => [Number(bus.id), bus.version]));
      changedVersions.clear();
      (payload.buses || []).forEach((bus) => {
        if (previous.has(Number(bus.id)) && previous.get(Number(bus.id)) !== bus.version) changedVersions.add(Number(bus.id));
      });
      operationsRevision = payload.revision || operationsRevision;
      config.currentPeriodId = payload.current_period_id;
      pendingQueue = payload.pending_queue || [];
      setBuses(payload.buses || []);
      updateKpis(payload);
      renderBusCards();
      renderPendingQueue();
      updateSyncStatus(payload.generated_label || new Date().toLocaleTimeString(), false);
      if (openBusId && busById.has(openBusId)) refreshOpenDrawer();
      if (manual) toast('Live operations refreshed.');
      window.setTimeout(() => changedVersions.clear(), 2600);
    } catch (_error) {
      updateSyncStatus('', true);
      if (manual) toast('Could not refresh live operations. Existing data was preserved.', 'error');
    } finally {
      polling = false;
      button?.querySelector('i')?.classList.remove('fa-spin');
    }
  }

  function pendingSeconds(until) {
    const target = Date.parse(until || '');
    return Number.isFinite(target) ? Math.max(0, Math.ceil((target - Date.now()) / 1000)) : 0;
  }

  function countdownLabel(until) {
    const seconds = pendingSeconds(until);
    if (!seconds) return 'Due now';
    return `Sends in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  }

  function updateCountdowns() {
    document.querySelectorAll('[data-pending-until]').forEach((node) => { node.textContent = countdownLabel(node.dataset.pendingUntil); });
  }

  function renderPendingQueue() {
    const section = byId('dashboard-pending-section');
    const list = byId('dashboard-pending-list');
    if (!section || !list) return;
    section.classList.toggle('hidden', pendingQueue.length === 0);
    list.replaceChildren();
    pendingQueue.forEach((incident) => {
      const row = makeElement('div', 'p-4 lg:px-5 flex flex-col lg:flex-row lg:items-center gap-3');
      const status = makeElement('div', 'min-w-0 flex-1');
      const title = makeElement('div', 'flex flex-wrap items-center gap-2');
      title.append(makeElement('span', 'font-black text-slate-800', incident.bus_label));
      const badge = makeElement('span', 'px-2 py-1 rounded-full text-white text-xs font-bold', incident.type);
      badge.style.background = incident.color;
      title.append(badge);
      status.append(title, makeElement('p', 'text-xs text-slate-500 mt-1', [incident.schedule, incident.delay ? `+${incident.delay} min` : '', incident.reason].filter(Boolean).join(' · ') || 'No additional details'));
      const countdown = makeElement('span', 'dashboard-countdown min-w-28 text-sm font-black text-purple-700');
      countdown.dataset.pendingUntil = incident.pending_until_utc || '';
      row.append(status, countdown);
      const actions = makeElement('div', 'flex flex-wrap gap-2');
      const open = makeElement('button', 'min-h-10 px-3 rounded-xl border border-slate-200 text-slate-600 text-xs font-bold', 'Open bus');
      open.type = 'button'; open.dataset.openBus = String(incident.bus_id); actions.append(open);
      if (config.canWrite) {
        [['confirm', 'Confirm now', 'bg-emerald-600 text-white'], ['correct', 'Correct', 'border border-blue-200 text-blue-700'], ['cancel', 'Cancel', 'border border-red-200 text-red-700']].forEach(([action, label, cls]) => {
          const button = makeElement('button', `min-h-10 px-3 rounded-xl text-xs font-bold ${cls}`, label);
          button.type = 'button'; button.dataset.pendingAction = action; button.dataset.incidentId = String(incident.id); actions.append(button);
        });
      }
      row.append(actions); list.append(row);
    });
    updateCountdowns();
  }

  function makeTimeline(bus) {
    const timeline = byId('drawer-timeline');
    const empty = byId('drawer-no-timeline');
    if (!timeline || !empty) return;
    timeline.replaceChildren();
    empty.classList.toggle('hidden', (bus.incidents || []).length > 0);
    (bus.incidents || []).forEach((incident) => {
      const row = makeElement('div', 'rounded-xl border border-slate-100 p-3');
      const header = makeElement('div', 'flex items-center justify-between gap-2');
      const label = makeElement('span', 'inline-flex items-center gap-1.5 text-xs font-bold', incident.type);
      label.style.color = incident.color;
      const time = makeElement('span', 'text-xs text-slate-400', `${incident.created_label}${incident.pending ? ' · ' + countdownLabel(incident.pending_until_utc) : ''}`);
      if (incident.pending) time.dataset.pendingUntil = incident.pending_until_utc || '';
      header.append(label, time); row.append(header);
      const details = [incident.delay ? `+${incident.delay} min` : '', incident.eta_label ? `ETA ${incident.eta_label}` : '', incident.schedule, incident.reason].filter(Boolean).join(' · ');
      if (details) row.append(makeElement('p', 'text-xs text-slate-500 mt-1', details));
      if (incident.notes) row.append(makeElement('p', 'text-xs text-slate-400 mt-1', incident.notes));
      timeline.append(row);
    });
  }

  function latestForSchedule(bus, scheduleId) {
    const target = scheduleId ? Number(scheduleId) : null;
    return (bus?.incidents || []).find((incident) => (incident.schedule_id || null) === target) || null;
  }

  function resetIncidentForm(bus) {
    if (!drawerForm) return;
    drawerForm.action = `/admin/buses/${bus.id}/incident`;
    drawerForm.querySelectorAll('input[type="radio"]').forEach((input) => { input.checked = false; });
    const values = {'#inc-delay': '0', '#inc-eta': '', '#incident-notes': '', '#inc-reason-select': '', '#inc-reason-text': '', '#incident-replace-id': ''};
    Object.entries(values).forEach(([selector, value]) => { const node = drawerForm.querySelector(selector); if (node) node.value = value; });
    const schedule = drawerForm.querySelector('#incident-schedule');
    if (schedule) schedule.value = config.currentPeriodId ? String(config.currentPeriodId) : '';
    const expected = drawerForm.querySelector('#incident-expected-latest');
    if (expected) expected.value = String(latestForSchedule(bus, schedule?.value || '')?.id || 0);
    const next = drawerForm.querySelector('#incident-next');
    if (next) {
      const nextUrl = new URL(config.dashboardUrl, window.location.origin);
      nextUrl.searchParams.set('bus', String(bus.id));
      next.value = nextUrl.pathname + nextUrl.search;
    }
    byId('inc-reason-custom')?.classList.add('hidden');
    byId('add-reason-panel')?.classList.add('hidden');
    drawerForm.classList.add('hidden');
    drawerToggleForm?.classList.remove('hidden');
  }

  function refreshOpenDrawer() {
    const bus = busById.get(Number(openBusId));
    if (!bus) { closeDrawer(); return; }
    setText('drawer-bus-title', bus.display_name); setText('drawer-bus-route', bus.route || 'No route specified');
    const status = byId('drawer-status');
    if (status) {
      const badge = makeElement('span', 'inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-white text-sm font-bold', bus.status);
      badge.style.background = bus.status_color; status.replaceChildren(badge);
    }
    setText('drawer-delay', bus.delay ? `+${bus.delay} min` : '');
    setText('drawer-eta', bus.eta_label ? `ETA ${bus.eta_label}` : '');
    setText('drawer-reason', bus.reason || (bus.is_attention ? 'No reason recorded.' : 'No current service exception.'));
    setText('drawer-schedules', (bus.schedules || []).length ? bus.schedules.map((item) => `${item.name}${item.departure_label ? ` ${item.departure_label}` : ''}`).join(', ') : 'Not assigned');
    setText('drawer-groups', `${bus.group_count || 0}${(bus.group_names || []).length ? ` · ${bus.group_names.join(', ')}` : ''}`);
    makeTimeline(bus);
    if (drawerForm?.classList.contains('hidden')) resetIncidentForm(bus);
    updateCountdowns();
  }

  function openDrawer(busId, trigger) {
    const bus = busById.get(Number(busId));
    if (!bus || !drawer || !overlay) return;
    openBusId = Number(busId); drawerReturnFocus = trigger || document.activeElement; refreshOpenDrawer();
    const preview = byId('drawer-recipient-preview');
    if (preview) { preview.classList.add('hidden'); preview.replaceChildren(); }
    drawer.hidden = false; overlay.hidden = false; drawer.setAttribute('aria-hidden', 'false'); overlay.setAttribute('aria-hidden', 'false'); document.body.style.overflow = 'hidden';
    window.requestAnimationFrame(() => { drawer.classList.add('is-open'); overlay.classList.add('is-open'); drawer.querySelector('[data-close-dashboard-drawer]')?.focus(); });
  }

  function closeDrawer() {
    if (!drawer || !overlay || drawer.hidden) return;
    openBusId = null; drawer.classList.remove('is-open'); overlay.classList.remove('is-open'); drawer.setAttribute('aria-hidden', 'true'); overlay.setAttribute('aria-hidden', 'true'); document.body.style.overflow = '';
    window.setTimeout(() => { drawer.hidden = true; overlay.hidden = true; }, 220); drawerReturnFocus?.focus?.();
  }

  function recipientPreviewMarkup(preview) {
    const container = makeElement('div', 'space-y-2');
    const summary = makeElement('div', 'grid grid-cols-2 sm:grid-cols-4 gap-2');
    [['Subscribers', preview.subscriber_count], ['Contacts', preview.contact_count], ['Emails', preview.email_count], ['SMS', preview.sms_count]].forEach(([label, value]) => {
      const card = makeElement('div', 'rounded-lg bg-white border border-blue-100 p-2');
      card.append(makeElement('p', 'text-lg font-black text-slate-800', String(value || 0)), makeElement('p', 'text-[10px] text-slate-500', label)); summary.append(card);
    });
    container.append(summary);
    const languages = Object.entries(preview.languages || {}).map(([key, value]) => `${key.toUpperCase()}: ${value}`).join(', ') || 'none';
    const roles = Object.entries(preview.roles || {}).map(([key, value]) => `${key}: ${value}`).join(', ') || 'none';
    const schools = Object.entries(preview.schools || {}).map(([key, value]) => `${key}: ${value}`).join(', ') || 'none';
    container.append(makeElement('p', 'text-xs text-slate-500', `Roles — ${roles} · Languages — ${languages} · Schools — ${schools}`));
    if (preview.buses_without_recipients) container.append(makeElement('p', 'text-xs font-bold text-amber-700', `${preview.buses_without_recipients} selected bus(es) have no assigned recipient scope.`));
    return container;
  }

  async function fetchRecipientPreview(busIds, target) {
    if (!config.recipientPreviewUrl || !busIds.length || !target) return;
    target.classList.remove('hidden'); target.classList.remove('text-red-700'); target.textContent = 'Calculating aggregate scope…';
    try {
      const response = await fetch(config.recipientPreviewUrl, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': config.csrfToken}, body: JSON.stringify({bus_ids: busIds, schedule_type_id: byId('bulk-schedule')?.value || config.currentPeriodId})});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || 'Preview unavailable');
      target.replaceChildren(recipientPreviewMarkup(data.preview));
    } catch (error) { target.textContent = error.message || 'Recipient preview unavailable.'; target.classList.add('text-red-700'); }
  }

  async function pendingAction(action, incidentId) {
    const incident = pendingQueue.find((item) => Number(item.id) === Number(incidentId));
    if (!incident) return refreshOperations(true);
    if (action === 'correct') {
      openDrawer(incident.bus_id, null); drawerToggleForm?.click();
      const type = drawerForm?.querySelector(`input[name="incident_type_id"][value="${incident.type_id}"]`); if (type) type.checked = true;
      const fields = [['incident-schedule', incident.schedule_id || ''], ['inc-delay', incident.delay || 0], ['inc-eta', incident.eta || ''], ['inc-reason-select', incident.reason_id || (incident.reason_text ? 'custom' : '')], ['inc-reason-text', incident.reason_text || ''], ['incident-notes', incident.notes || ''], ['incident-replace-id', incident.id], ['incident-expected-latest', incident.id]];
      fields.forEach(([id, value]) => { const node = byId(id); if (node) node.value = value; });
      if (incident.reason_text) byId('inc-reason-custom')?.classList.remove('hidden');
      return;
    }
    if (action === 'cancel' && !window.confirm(`Cancel the pending update for ${incident.bus_label}?`)) return;
    const button = document.querySelector(`[data-pending-action="${action}"][data-incident-id="${incident.id}"]`); if (button) button.disabled = true;
    try {
      const response = await fetch(`/admin/dashboard/incidents/${incident.id}/${action}`, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': config.csrfToken}, body: JSON.stringify({version: incident.version})});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || 'Operation failed');
      if (data.request_token) { const token = drawerForm?.querySelector('input[name="request_token"]'); if (token) token.value = data.request_token; }
      toast(data.message || 'Pending update processed.'); await refreshOperations(false);
    } catch (error) { toast(error.message || 'Could not process pending update.', 'error'); await refreshOperations(false); }
    finally { if (button) button.disabled = false; }
  }

  function toggleBulkMode(force) {
    bulkMode = typeof force === 'boolean' ? force : !bulkMode;
    byId('dashboard-bulk-toggle')?.classList.toggle('bg-slate-700', bulkMode); byId('dashboard-bulk-toggle')?.classList.toggle('bg-blue-600', !bulkMode);
    renderBusCards(); if (!bulkMode) clearSelection();
  }

  function updateSelectionUI() {
    document.querySelectorAll('[data-select-bus]').forEach((checkbox) => { checkbox.checked = selectedBusIds.has(Number(checkbox.dataset.selectBus)); });
    document.querySelectorAll('.dashboard-bus-card').forEach((card) => card.classList.toggle('is-selected', selectedBusIds.has(Number(card.dataset.busId))));
    setText('dashboard-selected-count', selectedBusIds.size); setText('dashboard-bulk-modal-count', selectedBusIds.size);
    byId('dashboard-bulk-bar')?.classList.toggle('hidden', !bulkMode);
    const open = byId('dashboard-open-bulk'); if (open) open.disabled = selectedBusIds.size === 0;
  }

  function clearSelection() { selectedBusIds.clear(); updateSelectionUI(); }
  function openBulkModal() { if (!selectedBusIds.size) return; byId('dashboard-bulk-modal')?.classList.remove('hidden'); byId('dashboard-bulk-error')?.classList.add('hidden'); if (byId('dashboard-bulk-confirm')) byId('dashboard-bulk-confirm').checked = false; document.body.style.overflow = 'hidden'; }
  function closeBulkModal() { byId('dashboard-bulk-modal')?.classList.add('hidden'); if (!drawer || drawer.hidden) document.body.style.overflow = ''; }

  async function submitBulk(event) {
    event.preventDefault();
    const error = byId('dashboard-bulk-error'); const submit = byId('dashboard-submit-bulk');
    if (!byId('dashboard-bulk-confirm')?.checked) { if (error) { error.textContent = 'Confirm that you reviewed the selection and status.'; error.classList.remove('hidden'); } return; }
    const scheduleId = byId('bulk-schedule')?.value || ''; const expected = {};
    selectedBusIds.forEach((id) => { expected[String(id)] = latestForSchedule(busById.get(id), scheduleId)?.id || 0; });
    const reason = byId('bulk-reason')?.value || '';
    const payload = {bus_ids: Array.from(selectedBusIds), confirmed: true, request_token: config.bulkToken, expected_latest_ids: expected, incident: {incident_type_id: byId('bulk-incident-type')?.value, schedule_type_id: scheduleId, delay_minutes: byId('bulk-delay')?.value || 0, eta: byId('bulk-eta')?.value || '', delay_reason_id: reason === 'custom' ? '' : reason, delay_reason_text: reason === 'custom' ? byId('bulk-reason-text')?.value || '' : '', notes: byId('bulk-notes')?.value || ''}};
    if (submit) { submit.disabled = true; submit.textContent = 'Staging…'; }
    try {
      const response = await fetch(config.bulkUrl, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': config.csrfToken}, body: JSON.stringify(payload)}); const data = await response.json();
      if (data.request_token) config.bulkToken = data.request_token;
      if (!response.ok || !data.ok) throw new Error(data.message || 'Bulk operation failed');
      closeBulkModal(); toast(data.message || 'Bulk updates staged.'); clearSelection(); toggleBulkMode(false); await refreshOperations(false);
    } catch (failure) { if (error) { error.textContent = failure.message || 'Bulk operation failed.'; error.classList.remove('hidden'); } await refreshOperations(false); }
    finally { if (submit) { submit.disabled = false; submit.textContent = 'Stage updates'; } }
  }

  document.addEventListener('click', (event) => {
    const open = event.target.closest('[data-open-bus]'); if (open && !event.target.closest('[data-select-bus]')) { openDrawer(open.dataset.openBus, open); return; }
    const action = event.target.closest('[data-pending-action]'); if (action) pendingAction(action.dataset.pendingAction, action.dataset.incidentId);
  });
  document.addEventListener('change', (event) => {
    const checkbox = event.target.closest('[data-select-bus]'); if (!checkbox) return;
    const id = Number(checkbox.dataset.selectBus); if (checkbox.checked) selectedBusIds.add(id); else selectedBusIds.delete(id); updateSelectionUI();
  });

  [search, statusFilter, scheduleFilter, routeFilter, groupFilter, schoolFilter].filter(Boolean).forEach((control) => control.addEventListener(control.tagName === 'INPUT' ? 'input' : 'change', () => { summaryMode = 'all'; applyFilters(); }));
  document.querySelectorAll('[data-summary-filter]').forEach((button) => button.addEventListener('click', () => { summaryMode = button.dataset.summaryFilter; applyFilters(); byId('operations-heading')?.scrollIntoView({behavior: 'smooth', block: 'start'}); }));
  byId('dashboard-refresh')?.addEventListener('click', () => refreshOperations(true));
  byId('dashboard-pending-shortcut')?.addEventListener('click', () => byId('dashboard-pending-section')?.scrollIntoView({behavior: 'smooth'}));
  byId('dashboard-save-view')?.addEventListener('click', saveView);
  savedView?.addEventListener('change', () => applySavedFilters(storedViews()[Number(savedView.value)]?.filters));
  filterToggle?.addEventListener('click', () => {
    const open = filterToggle.getAttribute('aria-expanded') !== 'true';
    filterToggle.setAttribute('aria-expanded', String(open));
    filterPanel?.classList.toggle('is-open', open);
  });
  byId('dashboard-bulk-toggle')?.addEventListener('click', () => toggleBulkMode());
  byId('dashboard-clear-selection')?.addEventListener('click', clearSelection);
  byId('dashboard-select-visible')?.addEventListener('click', () => { document.querySelectorAll('.dashboard-filterable:not(.hidden)').forEach((card) => selectedBusIds.add(Number(card.dataset.busId))); updateSelectionUI(); });
  byId('dashboard-open-bulk')?.addEventListener('click', openBulkModal);
  document.querySelectorAll('[data-close-bulk]').forEach((button) => button.addEventListener('click', closeBulkModal));
  byId('dashboard-bulk-form')?.addEventListener('submit', submitBulk);
  byId('bulk-reason')?.addEventListener('change', () => byId('bulk-reason-text')?.classList.toggle('hidden', byId('bulk-reason').value !== 'custom'));
  byId('dashboard-bulk-preview')?.addEventListener('click', () => fetchRecipientPreview(Array.from(selectedBusIds), byId('dashboard-bulk-recipient-preview')));
  byId('drawer-preview-recipients')?.addEventListener('click', () => fetchRecipientPreview([openBusId], byId('drawer-recipient-preview')));
  document.querySelectorAll('[data-close-dashboard-drawer]').forEach((button) => button.addEventListener('click', closeDrawer));
  overlay?.addEventListener('click', closeDrawer);
  drawerToggleForm?.addEventListener('click', () => { drawerToggleForm.classList.add('hidden'); drawerForm?.classList.remove('hidden'); drawerForm?.querySelector('input[type="radio"]')?.focus(); });
  drawerForm?.querySelectorAll('[data-close-incident]').forEach((button) => button.addEventListener('click', () => { drawerForm.classList.add('hidden'); drawerToggleForm?.classList.remove('hidden'); }));
  drawerForm?.addEventListener('submit', () => { const submit = drawerForm.querySelector('button[type="submit"]'); if (submit) { submit.disabled = true; submit.textContent = 'Recording…'; } });
  byId('incident-schedule')?.addEventListener('change', (event) => { const bus = busById.get(Number(openBusId)); const expected = byId('incident-expected-latest'); if (bus && expected) expected.value = String(latestForSchedule(bus, event.target.value)?.id || 0); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { if (!byId('dashboard-bulk-modal')?.classList.contains('hidden')) closeBulkModal(); else if (drawer && !drawer.hidden) closeDrawer(); } });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshOperations(false); });

  window.toggleCustomReason = function () {
    const select = byId('inc-reason-select'); byId('inc-reason-custom')?.classList.toggle('hidden', select?.value !== 'custom');
    if (select?.value !== 'custom' && byId('inc-reason-text')) byId('inc-reason-text').value = '';
  };
  window.showAddReason = function () { byId('add-reason-panel')?.classList.remove('hidden'); byId('new-reason-input')?.focus(); };
  window.submitNewReason = function () {
    const input = byId('new-reason-input'); if (!input?.value.trim()) return;
    fetch('/admin/delay-reasons/add', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF-Token': config.csrfToken}, body: `reason=${encodeURIComponent(input.value.trim())}`}).then((response) => response.json()).then((data) => {
      if (!data.success) return; const select = byId('inc-reason-select'); const custom = select?.querySelector('option[value="custom"]'); if (!select || !custom) return;
      select.insertBefore(new Option(data.reason, String(data.id)), custom); select.value = String(data.id); input.value = ''; byId('add-reason-panel')?.classList.add('hidden');
    });
  };

  function initializeCharts() {
    if (typeof Chart === 'undefined') return;
    const analytics = config.analytics || {}; const trend = byId('trendChart');
    if (trend && analytics.byDay && Object.keys(analytics.byDay).length) {
      const entries = Object.entries(analytics.byDay).sort(([left], [right]) => left.localeCompare(right));
      new Chart(trend, {type: 'line', data: {labels: entries.map(([day]) => day), datasets: [{data: entries.map(([, count]) => count), borderColor: '#2563eb', backgroundColor: 'rgba(37,99,235,.10)', fill: true, tension: .35}]}, options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}, scales: {y: {beginAtZero: true, ticks: {stepSize: 1}}}}});
    }
    const type = byId('typeChart');
    if (type && analytics.byType && Object.keys(analytics.byType).length) new Chart(type, {type: 'doughnut', data: {labels: Object.keys(analytics.byType), datasets: [{data: Object.values(analytics.byType), backgroundColor: analytics.typeColors, borderWidth: 2, borderColor: '#fff'}]}, options: {responsive: true, maintainAspectRatio: false, cutout: '62%', plugins: {legend: {position: 'right', labels: {boxWidth: 10, font: {size: 10}}}}}});
  }

  setBuses(config.buses || []); populateDynamicFilters(); renderSavedViews(); renderPendingQueue(); applyFilters(); initializeCharts(); updateCountdowns();
  window.setInterval(updateCountdowns, 1000);
  window.setInterval(() => refreshOperations(false), 25000);
  if (config.selectedBusId && busById.has(Number(config.selectedBusId))) openDrawer(Number(config.selectedBusId), null);
})();
