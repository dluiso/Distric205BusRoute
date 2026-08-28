(function (root) {
  'use strict';

  function normalizeSearch(value) {
    const spaced = String(value || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLocaleLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .trim()
      .replace(/\s+/g, ' ');
    return {spaced, compact: spaced.replace(/\s+/g, '')};
  }

  function matchesSearch(values, query) {
    const normalizedQuery = normalizeSearch(query);
    if (!normalizedQuery.spaced) return true;
    const haystack = normalizeSearch((values || []).join(' '));
    const tokensMatch = normalizedQuery.spaced
      .split(' ')
      .every((token) => haystack.spaced.includes(token));
    return tokensMatch || (
      normalizedQuery.compact.length > 1 &&
      haystack.compact.includes(normalizedQuery.compact)
    );
  }

  function cardPriority(card, favorites) {
    const attention = card.dataset.defaultStatus !== '1';
    const favorite = favorites.has(String(card.dataset.busId));
    return (attention ? 0 : 2) + (favorite ? 0 : 1);
  }

  function sortCards(cards, favorites, locale) {
    const collator = new Intl.Collator(locale || 'en-US', {
      numeric: true,
      sensitivity: 'base',
    });
    return [...cards].sort((left, right) => {
      const priority = cardPriority(left, favorites) - cardPriority(right, favorites);
      if (priority) return priority;
      const leftLabel = `${left.dataset.identifier || ''} ${left.dataset.name || ''}`;
      const rightLabel = `${right.dataset.identifier || ''} ${right.dataset.name || ''}`;
      return collator.compare(leftLabel, rightLabel);
    });
  }

  function groupKey(card, favorites, attentionOnly, favoritesOnly) {
    if (attentionOnly || card.dataset.defaultStatus !== '1') return 'affected';
    if (favoritesOnly || favorites.has(String(card.dataset.busId))) return 'favorites';
    return 'other';
  }

  function format(template, values) {
    return String(template || '').replace(/\{(\w+)\}/g, (_match, key) => (
      Object.prototype.hasOwnProperty.call(values, key) ? values[key] : `{${key}}`
    ));
  }

  function readStringSet(storage, key) {
    try {
      const value = JSON.parse(storage.getItem(key) || '[]');
      return new Set(Array.isArray(value) ? value.map(String) : []);
    } catch (_error) {
      return new Set();
    }
  }

  function initPublicPortal(documentRef, windowRef, config) {
    const strings = config.strings || {};
    const favoriteKey = 'bus_favs';
    const filterStateKey = 'bus_portal_filter_state';
    const themeKey = 'bustrack_theme';
    let favorites = readStringSet(windowRef.localStorage, favoriteKey);
    let showFavoritesOnly = false;
    let showAttentionOnly = false;
    let revision = config.revision || '';
    let operational = Boolean(config.operational);
    let districtDate = config.districtDate || '';
    let currentPeriodId = config.currentPeriodId == null ? null : String(config.currentPeriodId);
    let lastSuccessfulUpdate = new Date();
    let pollDelay = Number(config.pollIntervalMs) || 30000;
    let pollTimer = null;
    let polling = false;
    let deferredInstallPrompt = null;
    let sheetReturnFocus = null;

    const byId = (id) => documentRef.getElementById(id);
    const grid = () => byId('bus-grid');
    const cards = () => [...documentRef.querySelectorAll('.bus-card')];
    const busWord = (count) => count === 1 ? strings.bus : strings.buses;
    const searchControls = () => [byId('filter-search'), byId('mobile-search')].filter(Boolean);
    const statusControls = () => [byId('filter-status'), byId('sheet-status')].filter(Boolean);
    const scheduleControls = () => [byId('filter-schedule'), byId('sheet-schedule')].filter(Boolean);

    function canonicalValue(elements) {
      const selected = elements.find((element) => element.id.startsWith('filter-')) || elements[0];
      return selected ? selected.value : '';
    }

    function setControlValues(elements, value) {
      elements.forEach((element) => { element.value = value; });
    }

    function updateTheme(theme) {
      documentRef.documentElement.setAttribute('data-theme', theme);
      const icon = byId('theme-icon');
      if (icon) icon.className = theme === 'dark' ? 'fas fa-sun text-sm' : 'fas fa-moon text-sm';
      documentRef.querySelectorAll('[style*="border-color:rgba(0,0,0,.1)"]').forEach((element) => {
        element.style.borderColor = theme === 'dark' ? 'rgba(255,255,255,.12)' : 'rgba(0,0,0,.1)';
      });
    }

    function initTheme() {
      let saved = 'light';
      try { saved = windowRef.localStorage.getItem(themeKey) || 'light'; } catch (_error) { /* noop */ }
      updateTheme(saved === 'dark' ? 'dark' : 'light');
      const button = byId('theme-btn');
      if (button) button.addEventListener('click', () => {
        const next = documentRef.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        try { windowRef.localStorage.setItem(themeKey, next); } catch (_error) { /* noop */ }
        updateTheme(next);
      });
    }

    function updateClock() {
      const now = new Date();
      const dateElement = byId('nav-date');
      const clockElement = byId('nav-clock');
      const dateOptions = {weekday: 'short', month: 'short', day: 'numeric', timeZone: config.timeZone};
      const timeOptions = {
        hour: 'numeric', minute: '2-digit', second: '2-digit',
        hour12: !config.clock24h, timeZone: config.timeZone,
      };
      if (dateElement) dateElement.textContent = new Intl.DateTimeFormat(config.locale, dateOptions).format(now);
      if (clockElement) clockElement.textContent = new Intl.DateTimeFormat(config.locale, timeOptions).format(now);
    }

    function setFavoriteButton(button, isFavorite) {
      const icon = button.querySelector('i');
      const label = button.dataset.busLabel || '';
      button.setAttribute('aria-pressed', isFavorite ? 'true' : 'false');
      button.setAttribute('aria-label', format(
        isFavorite ? strings.remove_favorite_bus : strings.favorite_bus,
        {bus: label},
      ));
      button.title = isFavorite ? strings.remove_fav : strings.favorite;
      if (icon) icon.className = `fas fa-star ${isFavorite ? 'text-amber-300' : 'text-white'} text-sm`;
    }

    function initFavoriteButtons() {
      documentRef.querySelectorAll('.fav-btn').forEach((button) => {
        const id = String(button.dataset.busId);
        setFavoriteButton(button, favorites.has(id));
        if (button.dataset.favoriteListener !== '1') {
          button.dataset.favoriteListener = '1';
          button.addEventListener('click', () => {
            if (favorites.has(id)) favorites.delete(id); else favorites.add(id);
            try { windowRef.localStorage.setItem(favoriteKey, JSON.stringify([...favorites])); } catch (_error) { /* noop */ }
            setFavoriteButton(button, favorites.has(id));
            applyFilters();
          });
        }
      });
    }

    function currentAttentionCount(allCards) {
      return allCards.filter((card) => card.dataset.defaultStatus !== '1').length;
    }

    function currentFavoriteCount(allCards) {
      return allCards.filter((card) => favorites.has(String(card.dataset.busId))).length;
    }

    function updateBadge(element, count) {
      if (!element) return;
      element.textContent = String(count);
      element.classList.toggle('hidden', count === 0);
      element.classList.toggle('inline-flex', count > 0);
    }

    function updateNavigation(allCards) {
      const attentionCount = currentAttentionCount(allCards);
      const favoriteCount = currentFavoriteCount(allCards);
      updateBadge(byId('mobile-alert-badge'), attentionCount);
      updateBadge(byId('mobile-favorite-badge'), favoriteCount);
      documentRef.querySelectorAll('[data-nav-action]').forEach((button) => {
        const action = button.dataset.navAction;
        const active = (
          (action === 'alerts' && showAttentionOnly) ||
          (action === 'favorites' && showFavoritesOnly) ||
          (action === 'home' && !showAttentionOnly && !showFavoritesOnly)
        );
        if (active) button.setAttribute('aria-current', 'page');
        else button.removeAttribute('aria-current');
      });
      const activeMode = byId('mobile-active-mode');
      if (activeMode) {
        if (showAttentionOnly) activeMode.textContent = strings.affected_buses;
        else if (showFavoritesOnly) activeMode.textContent = strings.favorite_buses;
        else activeMode.textContent = '';
      }
    }

    function updateAttentionSummary(allCards) {
      const count = currentAttentionCount(allCards);
      const summary = byId('attention-summary');
      const message = byId('attention-message');
      const icon = byId('attention-icon');
      const button = byId('btn-attention');
      if (!summary || !message || !button) return;
      summary.classList.toggle('attention-alert', count > 0);
      summary.classList.toggle('attention-ok', count === 0);
      if (icon) icon.className = `fas ${count > 0 ? 'fa-triangle-exclamation' : 'fa-circle-check'}`;
      message.textContent = count > 0
        ? format(strings.attention_count, {count, bus_word: busWord(count)})
        : strings.all_on_time;
      button.classList.toggle('hidden', count === 0);
      button.classList.toggle('inline-flex', count > 0);
      if (count === 0) showAttentionOnly = false;
      button.setAttribute('aria-pressed', showAttentionOnly ? 'true' : 'false');
      const label = button.querySelector('span');
      if (label) label.textContent = showAttentionOnly ? strings.show_all : strings.show_affected;
    }

    function createGroupHeading(key) {
      const heading = documentRef.createElement('h2');
      heading.className = 'bus-group-heading';
      heading.dataset.group = key;
      heading.textContent = key === 'affected'
        ? strings.affected_buses
        : key === 'favorites' ? strings.favorite_buses : strings.other_buses;
      return heading;
    }

    function applyFilters() {
      const targetGrid = grid();
      if (!targetGrid) return;
      const allCards = cards();
      const search = canonicalValue(searchControls());
      const status = canonicalValue(statusControls());
      const schedule = canonicalValue(scheduleControls());
      const ordered = sortCards(allCards, favorites, config.locale);
      targetGrid.querySelectorAll('.bus-group-heading').forEach((heading) => heading.remove());
      ordered.forEach((card) => targetGrid.appendChild(card));

      let visible = 0;
      let previousGroup = null;
      ordered.forEach((card) => {
        const matchesText = matchesSearch([
          card.dataset.identifier, card.dataset.name, card.dataset.route,
          card.dataset.status, card.dataset.statusLabel,
          card.dataset.schedules, card.dataset.scheduleLabels,
        ], search);
        const matchesStatus = !status || card.dataset.status === status;
        const matchesSchedule = !schedule || (card.dataset.schedules || '').split(',').includes(schedule);
        const matchesFavorite = !showFavoritesOnly || favorites.has(String(card.dataset.busId));
        const matchesAttention = !showAttentionOnly || card.dataset.defaultStatus !== '1';
        const show = matchesText && matchesStatus && matchesSchedule && matchesFavorite && matchesAttention;
        card.classList.toggle('hidden-card', !show);
        if (!show) return;
        visible += 1;
        const nextGroup = groupKey(card, favorites, showAttentionOnly, showFavoritesOnly);
        if (nextGroup !== previousGroup) {
          targetGrid.insertBefore(createGroupHeading(nextGroup), card);
          previousGroup = nextGroup;
        }
      });

      const resultText = format(strings.results_count, {count: visible, bus_word: busWord(visible)});
      [byId('result-count'), byId('mobile-result-count')].filter(Boolean)
        .forEach((element) => { element.textContent = resultText; });
      const noResults = byId('no-results');
      if (noResults) noResults.classList.toggle('hidden', allCards.length === 0 || visible > 0);
      const noBuses = byId('no-buses');
      if (noBuses) noBuses.classList.toggle('hidden', allCards.length > 0);
      const hasSearch = Boolean(search);
      [byId('clear-search'), byId('mobile-clear-search')].filter(Boolean)
        .forEach((button) => button.classList.toggle('hidden', !hasSearch));
      updateAttentionSummary(allCards);
      updateNavigation(allCards);
      updateToggleButtons();
    }

    function syncAndApply(source, controls) {
      setControlValues(controls, source.value);
      applyFilters();
    }

    function restoreFilterState() {
      try {
        const restored = JSON.parse(windowRef.sessionStorage.getItem(filterStateKey) || 'null');
        windowRef.sessionStorage.removeItem(filterStateKey);
        if (!restored) return;
        setControlValues(searchControls(), restored.search || '');
        setControlValues(statusControls(), restored.status || '');
        if (!scheduleControls().some((element) => element.disabled)) {
          setControlValues(scheduleControls(), restored.schedule || '');
        }
        showFavoritesOnly = Boolean(restored.showFavoritesOnly);
        showAttentionOnly = Boolean(restored.showAttentionOnly);
      } catch (_error) { /* ignore stale browser state */ }
    }

    function preserveFilterState() {
      try {
        windowRef.sessionStorage.setItem(filterStateKey, JSON.stringify({
          search: canonicalValue(searchControls()),
          status: canonicalValue(statusControls()),
          schedule: canonicalValue(scheduleControls()),
          showFavoritesOnly,
          showAttentionOnly,
        }));
      } catch (_error) { /* noop */ }
    }

    function updateToggleButtons() {
      const favoriteButton = byId('btn-favs');
      if (favoriteButton) {
        favoriteButton.setAttribute('aria-pressed', showFavoritesOnly ? 'true' : 'false');
        favoriteButton.classList.toggle('bg-amber-50', showFavoritesOnly);
        favoriteButton.classList.toggle('border-amber-300', showFavoritesOnly);
      }
    }

    function resetFilters() {
      setControlValues(searchControls(), '');
      setControlValues(statusControls(), '');
      if (!scheduleControls().some((element) => element.disabled)) setControlValues(scheduleControls(), '');
      showFavoritesOnly = false;
      showAttentionOnly = false;
      applyFilters();
    }

    function scrollToResults() {
      const results = byId('bus-results');
      if (results) results.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function openFilterSheet(trigger) {
      const sheet = byId('filter-sheet');
      const backdrop = byId('filter-sheet-backdrop');
      if (!sheet || !backdrop) return;
      sheetReturnFocus = trigger || documentRef.activeElement;
      sheet.hidden = false;
      backdrop.hidden = false;
      sheet.setAttribute('aria-hidden', 'false');
      if (trigger) trigger.setAttribute('aria-expanded', 'true');
      documentRef.body.style.overflow = 'hidden';
      windowRef.requestAnimationFrame(() => {
        sheet.classList.add('is-open');
        backdrop.classList.add('is-open');
        const close = byId('close-filter-sheet');
        if (close) close.focus();
      });
    }

    function closeFilterSheet() {
      const sheet = byId('filter-sheet');
      const backdrop = byId('filter-sheet-backdrop');
      if (!sheet || !backdrop || sheet.hidden) return;
      sheet.classList.remove('is-open');
      backdrop.classList.remove('is-open');
      sheet.setAttribute('aria-hidden', 'true');
      if (sheetReturnFocus) sheetReturnFocus.setAttribute('aria-expanded', 'false');
      documentRef.body.style.overflow = '';
      windowRef.setTimeout(() => {
        sheet.hidden = true;
        backdrop.hidden = true;
      }, 250);
      if (sheetReturnFocus && typeof sheetReturnFocus.focus === 'function') sheetReturnFocus.focus();
    }

    function bindFilters() {
      searchControls().forEach((element) => element.addEventListener('input', () => syncAndApply(element, searchControls())));
      statusControls().forEach((element) => element.addEventListener('change', () => syncAndApply(element, statusControls())));
      scheduleControls().forEach((element) => element.addEventListener('change', () => syncAndApply(element, scheduleControls())));
      [byId('clear-search'), byId('mobile-clear-search')].filter(Boolean).forEach((button) => {
        button.addEventListener('click', () => {
          setControlValues(searchControls(), '');
          const preferred = button.id === 'mobile-clear-search' ? byId('mobile-search') : byId('filter-search');
          if (preferred) preferred.focus();
          applyFilters();
        });
      });
      const favoriteButton = byId('btn-favs');
      if (favoriteButton) favoriteButton.addEventListener('click', () => {
        showFavoritesOnly = !showFavoritesOnly;
        if (showFavoritesOnly) showAttentionOnly = false;
        applyFilters();
      });
      const attentionButton = byId('btn-attention');
      if (attentionButton) attentionButton.addEventListener('click', () => {
        showAttentionOnly = !showAttentionOnly;
        if (showAttentionOnly) showFavoritesOnly = false;
        applyFilters();
        scrollToResults();
      });
      documentRef.querySelectorAll('[data-nav-action]').forEach((button) => {
        button.addEventListener('click', () => {
          const action = button.dataset.navAction;
          if (action === 'filters') {
            openFilterSheet(button);
            return;
          }
          if (action === 'home') resetFilters();
          if (action === 'alerts') {
            showAttentionOnly = currentAttentionCount(cards()) > 0;
            showFavoritesOnly = false;
            applyFilters();
          }
          if (action === 'favorites') {
            showFavoritesOnly = true;
            showAttentionOnly = false;
            applyFilters();
          }
          scrollToResults();
        });
      });
      const close = byId('close-filter-sheet');
      const backdrop = byId('filter-sheet-backdrop');
      const applyMobile = byId('apply-mobile-filters');
      const resetMobile = byId('reset-mobile-filters');
      if (close) close.addEventListener('click', closeFilterSheet);
      if (backdrop) backdrop.addEventListener('click', closeFilterSheet);
      if (applyMobile) applyMobile.addEventListener('click', () => { closeFilterSheet(); scrollToResults(); });
      if (resetMobile) resetMobile.addEventListener('click', resetFilters);
      documentRef.addEventListener('keydown', (event) => {
        const sheet = byId('filter-sheet');
        if (!sheet || sheet.hidden) return;
        if (event.key === 'Escape') {
          closeFilterSheet();
          return;
        }
        if (event.key !== 'Tab') return;
        const focusable = Array.from(sheet.querySelectorAll(
          'button:not([disabled]), select:not([disabled]), input:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
        )).filter((element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true');
        if (!focusable.length) {
          event.preventDefault();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && documentRef.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && documentRef.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      });
    }

    function setLiveState(state) {
      const dot = byId('live-dot');
      const label = byId('live-label');
      if (!dot || !label) return;
      dot.classList.toggle('reconnecting', state === 'reconnecting');
      dot.classList.toggle('interrupted', state === 'interrupted');
      label.textContent = state === 'reconnecting'
        ? strings.reconnecting
        : state === 'interrupted' ? strings.connection_interrupted : strings.live;
    }

    function updateLastUpdated() {
      const element = byId('last-updated');
      if (!element) return;
      const seconds = Math.max(0, Math.floor((Date.now() - lastSuccessfulUpdate.getTime()) / 1000));
      if (seconds < 5) element.textContent = strings.updated_just_now;
      else if (seconds < 60) element.textContent = format(strings.updated_seconds, {count: seconds});
      else element.textContent = format(strings.updated_minutes, {count: Math.floor(seconds / 60)});
    }

    function schedulePoll(delay) {
      windowRef.clearTimeout(pollTimer);
      pollTimer = windowRef.setTimeout(poll, delay);
    }

    async function poll() {
      if (polling) return;
      if (documentRef.hidden) {
        schedulePoll(pollDelay);
        return;
      }
      polling = true;
      try {
        const headers = revision ? {'If-None-Match': `"${revision}"`} : {};
        const response = await windowRef.fetch(config.apiUrl, {
          headers,
          cache: 'no-store',
          credentials: 'same-origin',
        });
        if (response.status === 304) {
          lastSuccessfulUpdate = new Date();
        } else {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const state = await response.json();
          const nextPeriodId = state.current_period == null ? null : String(state.current_period.id);
          if (Boolean(state.operational) !== operational ||
              nextPeriodId !== currentPeriodId ||
              state.district_date !== districtDate) {
            preserveFilterState();
            windowRef.location.reload();
            return;
          }
          if (state.revision !== revision && typeof state.cards_html === 'string' && grid()) {
            grid().innerHTML = state.cards_html;
            revision = state.revision;
            initFavoriteButtons();
            applyFilters();
          }
          lastSuccessfulUpdate = new Date();
        }
        pollDelay = Number(config.pollIntervalMs) || 30000;
        setLiveState('live');
      } catch (_error) {
        pollDelay = Math.min(Math.max(pollDelay * 2, 30000), 120000);
        setLiveState(pollDelay >= 120000 ? 'interrupted' : 'reconnecting');
      } finally {
        polling = false;
        updateLastUpdated();
        schedulePoll(pollDelay);
      }
    }

    function hideInstallPanel() {
      const panel = byId('install-app-panel');
      if (panel) panel.classList.add('hidden');
    }

    function initPwa() {
      if ('serviceWorker' in windowRef.navigator && config.serviceWorkerUrl) {
        windowRef.addEventListener('load', () => {
          windowRef.navigator.serviceWorker.register(config.serviceWorkerUrl, {scope: '/'}).catch(() => {});
        });
      }
      windowRef.addEventListener('beforeinstallprompt', (event) => {
        event.preventDefault();
        deferredInstallPrompt = event;
        const panel = byId('install-app-panel');
        if (panel) panel.classList.remove('hidden');
      });
      const installButton = byId('install-app');
      if (installButton) installButton.addEventListener('click', async () => {
        if (!deferredInstallPrompt) return;
        deferredInstallPrompt.prompt();
        await deferredInstallPrompt.userChoice;
        deferredInstallPrompt = null;
        hideInstallPanel();
      });
      windowRef.addEventListener('appinstalled', hideInstallPanel);
      windowRef.addEventListener('online', () => { setLiveState('live'); schedulePoll(0); });
      windowRef.addEventListener('offline', () => setLiveState('interrupted'));
    }

    initTheme();
    initPwa();
    updateClock();
    windowRef.setInterval(updateClock, 1000);
    windowRef.setInterval(updateLastUpdated, 1000);
    restoreFilterState();
    bindFilters();
    initFavoriteButtons();
    applyFilters();
    schedulePoll(pollDelay);
    documentRef.addEventListener('visibilitychange', () => {
      if (!documentRef.hidden) schedulePoll(0);
    });

    return {applyFilters, poll, resetFilters, openFilterSheet, closeFilterSheet};
  }

  const exported = {normalizeSearch, matchesSearch, cardPriority, sortCards, groupKey, format};
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  root.BusPortal = Object.assign(root.BusPortal || {}, exported, {initPublicPortal});

  if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
      const configNode = document.getElementById('public-portal-config');
      if (!configNode) return;
      try {
        initPublicPortal(document, window, JSON.parse(configNode.textContent));
      } catch (error) {
        console.error('Unable to initialize the public bus portal.', error);
      }
    });
  }
}(typeof window !== 'undefined' ? window : globalThis));
