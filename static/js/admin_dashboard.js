(function () {
  'use strict';

  const configElement = document.getElementById('dashboard-config');
  if (!configElement) return;
  const config = JSON.parse(configElement.textContent || '{}');
  const busById = new Map((config.buses || []).map((bus) => [Number(bus.id), bus]));
  const search = document.getElementById('dashboard-bus-search');
  const statusFilter = document.getElementById('dashboard-status-filter');
  const scheduleFilter = document.getElementById('dashboard-schedule-filter');
  const resultStatus = document.getElementById('dashboard-filter-status');
  const noResults = document.getElementById('dashboard-no-results');
  const attentionSection = document.getElementById('attention-bus-section');
  const onTimeSection = document.getElementById('on-time-bus-section');
  const cards = Array.from(document.querySelectorAll('.dashboard-filterable'));
  let summaryMode = 'all';
  let drawerReturnFocus = null;

  function normalize(value) {
    return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }

  function matchesSearch(card, query) {
    if (!query) return true;
    const haystack = normalize(card.dataset.search);
    const compactHaystack = haystack.replace(/\s+/g, '');
    const compactQuery = query.replace(/\s+/g, '');
    return query.split(' ').every((token) => haystack.includes(token)) ||
      compactHaystack.includes(compactQuery);
  }

  function applyFilters() {
    const query = normalize(search ? search.value : '');
    const selectedStatus = statusFilter ? statusFilter.value : '';
    const selectedSchedule = scheduleFilter ? scheduleFilter.value : '';
    let visible = 0;
    let visibleAttention = 0;
    let visibleOnTime = 0;
    cards.forEach((card) => {
      const isAttention = card.dataset.attention === '1';
      const isPending = card.dataset.pending === '1';
      const matchesMode = summaryMode === 'all' ||
        (summaryMode === 'attention' && isAttention) ||
        (summaryMode === 'pending' && isPending) ||
        (summaryMode === 'on-time' && !isAttention);
      const matches = matchesMode && matchesSearch(card, query) &&
        (!selectedStatus || card.dataset.status === selectedStatus) &&
        (!selectedSchedule || card.dataset.schedule.includes(selectedSchedule));
      card.hidden = !matches;
      card.classList.toggle('hidden', !matches);
      if (matches) {
        visible += 1;
        if (isAttention) visibleAttention += 1;
        else visibleOnTime += 1;
      }
    });
    if (attentionSection) attentionSection.classList.toggle('hidden', visibleAttention === 0);
    if (onTimeSection) {
      onTimeSection.classList.toggle('hidden', visibleOnTime === 0);
      if (query || selectedStatus || selectedSchedule || summaryMode === 'on-time') {
        onTimeSection.open = visibleOnTime > 0;
      }
    }
    if (noResults) noResults.classList.toggle('hidden', visible !== 0);
    if (resultStatus) resultStatus.textContent = `${visible} of ${cards.length} buses shown`;
    document.querySelectorAll('[data-summary-filter]').forEach((button) => {
      const active = button.dataset.summaryFilter === summaryMode;
      button.setAttribute('aria-pressed', String(active));
      button.classList.toggle('ring-2', active);
      button.classList.toggle('ring-blue-400', active);
    });
  }

  [search, statusFilter, scheduleFilter].filter(Boolean).forEach((control) => {
    control.addEventListener(control.tagName === 'INPUT' ? 'input' : 'change', () => {
      summaryMode = 'all';
      applyFilters();
    });
  });
  document.querySelectorAll('[data-summary-filter]').forEach((button) => {
    button.addEventListener('click', () => {
      summaryMode = button.dataset.summaryFilter;
      if (search) search.value = '';
      if (statusFilter) statusFilter.value = '';
      if (scheduleFilter) scheduleFilter.value = '';
      applyFilters();
      const operations = document.getElementById('operations-heading');
      if (operations) operations.scrollIntoView({behavior: 'smooth', block: 'start'});
    });
  });

  const drawer = document.getElementById('dashboard-bus-drawer');
  const overlay = document.getElementById('dashboard-drawer-overlay');
  const drawerForm = document.getElementById('dashboard-incident-form');
  const drawerToggleForm = document.getElementById('drawer-toggle-form');

  function text(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value || '';
  }

  function makeTimeline(bus) {
    const timeline = document.getElementById('drawer-timeline');
    const empty = document.getElementById('drawer-no-timeline');
    if (!timeline || !empty) return;
    timeline.replaceChildren();
    empty.classList.toggle('hidden', bus.incidents.length > 0);
    bus.incidents.forEach((incident) => {
      const row = document.createElement('div');
      row.className = 'rounded-xl border border-slate-100 p-3';
      const header = document.createElement('div');
      header.className = 'flex items-center justify-between gap-2';
      const label = document.createElement('span');
      label.className = 'inline-flex items-center gap-1.5 text-xs font-bold';
      label.style.color = incident.color;
      const icon = document.createElement('i');
      icon.className = `fas ${incident.icon}`;
      icon.setAttribute('aria-hidden', 'true');
      label.append(icon, document.createTextNode(incident.type));
      const time = document.createElement('span');
      time.className = 'text-xs text-slate-400';
      time.textContent = `${incident.created_label}${incident.pending ? ' · Pending' : ''}`;
      header.append(label, time);
      row.append(header);
      const details = [
        incident.delay ? `+${incident.delay} min` : '',
        incident.eta_label ? `ETA ${incident.eta_label}` : '',
        incident.schedule,
        incident.reason,
      ].filter(Boolean).join(' · ');
      if (details) {
        const detail = document.createElement('p');
        detail.className = 'text-xs text-slate-500 mt-1';
        detail.textContent = details;
        row.append(detail);
      }
      if (incident.notes) {
        const notes = document.createElement('p');
        notes.className = 'text-xs text-slate-400 mt-1';
        notes.textContent = incident.notes;
        row.append(notes);
      }
      timeline.append(row);
    });
  }

  function resetIncidentForm(bus) {
    if (!drawerForm) return;
    drawerForm.action = `/admin/buses/${bus.id}/incident`;
    drawerForm.querySelectorAll('input[type="radio"]').forEach((input) => { input.checked = false; });
    const delay = drawerForm.querySelector('#inc-delay');
    const eta = drawerForm.querySelector('#inc-eta');
    const notes = drawerForm.querySelector('#incident-notes');
    const reason = drawerForm.querySelector('#inc-reason-select');
    const customReason = drawerForm.querySelector('#inc-reason-text');
    const schedule = drawerForm.querySelector('#incident-schedule');
    if (delay) delay.value = '0';
    if (eta) eta.value = '';
    if (notes) notes.value = '';
    if (reason) reason.value = '';
    if (customReason) customReason.value = '';
    if (schedule) schedule.value = config.currentPeriodId ? String(config.currentPeriodId) : '';
    const next = drawerForm.querySelector('#incident-next');
    if (next) {
      const nextUrl = new URL(config.dashboardUrl, window.location.origin);
      nextUrl.searchParams.set('bus', String(bus.id));
      next.value = nextUrl.pathname + nextUrl.search;
    }
    const customPanel = drawerForm.querySelector('#inc-reason-custom');
    const addPanel = drawerForm.querySelector('#add-reason-panel');
    if (customPanel) customPanel.classList.add('hidden');
    if (addPanel) addPanel.classList.add('hidden');
    drawerForm.classList.add('hidden');
    if (drawerToggleForm) drawerToggleForm.classList.remove('hidden');
  }

  function openDrawer(busId, trigger) {
    const bus = busById.get(Number(busId));
    if (!bus || !drawer || !overlay) return;
    drawerReturnFocus = trigger || document.activeElement;
    text('drawer-bus-title', bus.display_name);
    text('drawer-bus-route', bus.route || 'No route specified');
    const status = document.getElementById('drawer-status');
    if (status) {
      status.replaceChildren();
      const badge = document.createElement('span');
      badge.className = 'inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-white text-sm font-bold';
      badge.style.background = bus.status_color;
      const icon = document.createElement('i');
      icon.className = `fas ${bus.status_icon}`;
      icon.setAttribute('aria-hidden', 'true');
      badge.append(icon, document.createTextNode(bus.status));
      status.append(badge);
    }
    text('drawer-delay', bus.delay ? `+${bus.delay} min` : '');
    text('drawer-eta', bus.eta_label ? `ETA ${bus.eta_label}` : '');
    text('drawer-reason', bus.reason || (bus.is_attention ? 'No reason recorded.' : 'No current service exception.'));
    text('drawer-schedules', bus.schedules.length ? bus.schedules.map((item) =>
      `${item.name}${item.departure_label ? ` ${item.departure_label}` : ''}`).join(', ') : 'Not assigned');
    text('drawer-groups', String(bus.group_count || 0));
    makeTimeline(bus);
    resetIncidentForm(bus);
    drawer.hidden = false;
    overlay.hidden = false;
    drawer.setAttribute('aria-hidden', 'false');
    overlay.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    window.requestAnimationFrame(() => {
      drawer.classList.add('is-open');
      overlay.classList.add('is-open');
      const close = drawer.querySelector('[data-close-dashboard-drawer]');
      if (close) close.focus();
    });
  }

  function closeDrawer() {
    if (!drawer || !overlay || drawer.hidden) return;
    drawer.classList.remove('is-open');
    overlay.classList.remove('is-open');
    drawer.setAttribute('aria-hidden', 'true');
    overlay.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    window.setTimeout(() => { drawer.hidden = true; overlay.hidden = true; }, 220);
    if (drawerReturnFocus && typeof drawerReturnFocus.focus === 'function') drawerReturnFocus.focus();
  }

  document.querySelectorAll('[data-open-bus]').forEach((button) => {
    button.addEventListener('click', () => openDrawer(button.dataset.openBus, button));
  });
  document.querySelectorAll('[data-close-dashboard-drawer]').forEach((button) => button.addEventListener('click', closeDrawer));
  if (overlay) overlay.addEventListener('click', closeDrawer);
  if (drawerToggleForm && drawerForm) drawerToggleForm.addEventListener('click', () => {
    drawerToggleForm.classList.add('hidden');
    drawerForm.classList.remove('hidden');
    const first = drawerForm.querySelector('input[type="radio"]');
    if (first) first.focus();
  });
  if (drawerForm) {
    drawerForm.querySelectorAll('[data-close-incident]').forEach((button) => button.addEventListener('click', () => {
      drawerForm.classList.add('hidden');
      if (drawerToggleForm) { drawerToggleForm.classList.remove('hidden'); drawerToggleForm.focus(); }
    }));
    drawerForm.addEventListener('submit', () => {
      const submit = drawerForm.querySelector('button[type="submit"]');
      if (submit) { submit.disabled = true; submit.textContent = 'Recording…'; }
    });
  }

  document.addEventListener('keydown', (event) => {
    if (!drawer || drawer.hidden) return;
    if (event.key === 'Escape') { closeDrawer(); return; }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(drawer.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]'))
      .filter((element) => !element.hidden && !element.closest('.hidden'));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });

  window.toggleCustomReason = function () {
    const select = document.getElementById('inc-reason-select');
    const panel = document.getElementById('inc-reason-custom');
    const input = document.getElementById('inc-reason-text');
    if (!select || !panel) return;
    panel.classList.toggle('hidden', select.value !== 'custom');
    if (select.value !== 'custom' && input) input.value = '';
  };
  window.showAddReason = function () {
    const panel = document.getElementById('add-reason-panel');
    const input = document.getElementById('new-reason-input');
    if (panel) panel.classList.remove('hidden');
    if (input) input.focus();
  };
  window.submitNewReason = function () {
    const input = document.getElementById('new-reason-input');
    if (!input || !input.value.trim()) return;
    fetch('/admin/delay-reasons/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: `reason=${encodeURIComponent(input.value.trim())}`,
    }).then((response) => response.json()).then((data) => {
      if (!data.success) return;
      const select = document.getElementById('inc-reason-select');
      const custom = select ? select.querySelector('option[value="custom"]') : null;
      if (!select || !custom) return;
      const option = document.createElement('option');
      option.value = String(data.id);
      option.textContent = data.reason;
      select.insertBefore(option, custom);
      select.value = String(data.id);
      input.value = '';
      document.getElementById('add-reason-panel').classList.add('hidden');
    });
  };

  function initializeCharts() {
    if (typeof Chart === 'undefined') return;
    const analytics = config.analytics || {};
    const trend = document.getElementById('trendChart');
    if (trend && analytics.byDay && Object.keys(analytics.byDay).length) {
      const entries = Object.entries(analytics.byDay).sort(([left], [right]) => left.localeCompare(right));
      new Chart(trend, {type: 'line', data: {labels: entries.map(([day]) => day), datasets: [{data: entries.map(([, count]) => count), borderColor: '#2563eb', backgroundColor: 'rgba(37,99,235,.10)', fill: true, tension: .35}]}, options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}, scales: {y: {beginAtZero: true, ticks: {stepSize: 1}}}}});
    }
    const type = document.getElementById('typeChart');
    if (type && analytics.byType && Object.keys(analytics.byType).length) {
      new Chart(type, {type: 'doughnut', data: {labels: Object.keys(analytics.byType), datasets: [{data: Object.values(analytics.byType), backgroundColor: analytics.typeColors, borderWidth: 2, borderColor: '#fff'}]}, options: {responsive: true, maintainAspectRatio: false, cutout: '62%', plugins: {legend: {position: 'right', labels: {boxWidth: 10, font: {size: 10}}}}}});
    }
  }

  applyFilters();
  initializeCharts();
  if (config.selectedBusId && busById.has(Number(config.selectedBusId))) {
    openDrawer(Number(config.selectedBusId), null);
  }
})();
