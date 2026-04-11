<svelte:options runes={false} />
<script>
  import { onDestroy, onMount } from 'svelte';
  import {
    BASE_URL,
    deleteSnapshot,
    executeSnapshotAction,
    getSnapshotApps,
    getSnapshotHistory,
    getSnapshotSettings
  } from '../api.js';
  import { DATE_TIME_FORMATS, formatDateTime, parseDateInput } from './dateTime.js';

  let loading = false;
  let busyActionId = null;
  let deletingId = null;
  let error = '';

  let limit = 400;

  const FILTER_MODE_NONE = 'none';
  const FILTER_MODE_YEAR = 'year';
  const FILTER_MODE_MONTH = 'month';
  const FILTER_MODE_WEEK = 'week';
  const FILTER_MODE_DAY = 'day';
  const FILTER_MODE_TIME_OF_DAY = 'time_of_day';
  const FILTER_APP_ALL = '__all_apps__';

  const TIME_OF_DAY_BUCKETS = [
    { value: 'morning', label: 'Morning (05:00-11:59)' },
    { value: 'afternoon', label: 'Afternoon (12:00-16:59)' },
    { value: 'evening', label: 'Evening (17:00-20:59)' },
    { value: 'night', label: 'Night (21:00-04:59)' }
  ];

  const nowLocal = new Date();
  const pad2 = (value) => String(value).padStart(2, '0');
  const toDateInputValue = (date) => `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
  const toMonthInputValue = (date) => `${date.getFullYear()}-${pad2(date.getMonth() + 1)}`;

  const toIsoWeekInputValue = (date) => {
    const working = new Date(date);
    working.setHours(0, 0, 0, 0);
    working.setDate(working.getDate() + 3 - ((working.getDay() + 6) % 7));
    const weekOne = new Date(working.getFullYear(), 0, 4);
    const week =
      1 + Math.round(((working.getTime() - weekOne.getTime()) / 86400000 - 3 + ((weekOne.getDay() + 6) % 7)) / 7);
    return `${working.getFullYear()}-W${pad2(week)}`;
  };

  const parseSnapshotDate = (value) => {
    return parseDateInput(value);
  };

  const parseIsoWeekInput = (value) => {
    const match = /^([0-9]{4})-W([0-9]{2})$/.exec(String(value || ''));
    if (!match) return null;
    const year = Number(match[1]);
    const week = Number(match[2]);
    if (!Number.isFinite(year) || !Number.isFinite(week) || week < 1 || week > 53) {
      return null;
    }

    const jan4 = new Date(year, 0, 4);
    const jan4Day = jan4.getDay() || 7;
    const mondayOfWeekOne = new Date(year, 0, 4 - (jan4Day - 1));
    mondayOfWeekOne.setHours(0, 0, 0, 0);

    const start = new Date(mondayOfWeekOne);
    start.setDate(mondayOfWeekOne.getDate() + (week - 1) * 7);
    const end = new Date(start);
    end.setDate(start.getDate() + 7);

    return { start, end };
  };

  const isInTimeOfDayBucket = (date, bucketValue) => {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return false;
    const hour = date.getHours();
    if (bucketValue === 'morning') return hour >= 5 && hour <= 11;
    if (bucketValue === 'afternoon') return hour >= 12 && hour <= 16;
    if (bucketValue === 'evening') return hour >= 17 && hour <= 20;
    return hour >= 21 || hour <= 4;
  };

  let filterMode = FILTER_MODE_NONE;
  let filterApp = FILTER_APP_ALL;
  let appFilterOptions = [];
  let filterYear = String(nowLocal.getFullYear());
  let filterMonth = toMonthInputValue(nowLocal);
  let filterWeek = toIsoWeekInputValue(nowLocal);
  let filterDay = toDateInputValue(nowLocal);
  let filterTimeOfDay = TIME_OF_DAY_BUCKETS[0].value;

  let allowDelete = false;
  let items = [];
  let timelineItems = [];
  let scrubIndex = 0;
  let scrubDraftIndex = 0;
  let resolvedImageSrc = '';
  let imageLoading = false;
  let imageObjectUrlsById = new Map();
  let imageLoadToken = 0;
  let isScrubbing = false;
  const IMAGE_OBJECT_URL_CACHE_LIMIT = 80;

  const formatTime = (value) => {
    return formatDateTime(value, DATE_TIME_FORMATS.MEDIUM_DATE_TIME);
  };

  const timelineLabel = (item) => {
    if (!item) return 'No snapshot';
    return `${formatTime(item.captured_at)} - ${item.app_name || 'Unknown app'}`;
  };

  const imageUrl = (item) => {
    if (!item?.image_available || !item?.image_endpoint) return null;
    return `${BASE_URL}${item.image_endpoint}`;
  };

  const buildHistoryTimeRange = () => {
    if (filterMode === FILTER_MODE_NONE || filterMode === FILTER_MODE_TIME_OF_DAY) {
      return { start_time: null, end_time: null };
    }

    if (filterMode === FILTER_MODE_YEAR) {
      const year = Number(filterYear);
      if (!Number.isFinite(year) || year < 1970 || year > 9999) {
        throw new Error('Invalid year filter.');
      }
      const start = new Date(year, 0, 1, 0, 0, 0, 0);
      const end = new Date(year + 1, 0, 1, 0, 0, 0, 0);
      return { start_time: start.toISOString(), end_time: end.toISOString() };
    }

    if (filterMode === FILTER_MODE_MONTH) {
      const match = /^([0-9]{4})-([0-9]{2})$/.exec(String(filterMonth || ''));
      if (!match) {
        throw new Error('Invalid month filter.');
      }
      const year = Number(match[1]);
      const month = Number(match[2]);
      const start = new Date(year, month - 1, 1, 0, 0, 0, 0);
      const end = new Date(year, month, 1, 0, 0, 0, 0);
      return { start_time: start.toISOString(), end_time: end.toISOString() };
    }

    if (filterMode === FILTER_MODE_WEEK) {
      const parsed = parseIsoWeekInput(filterWeek);
      if (!parsed) {
        throw new Error('Invalid week filter.');
      }
      return {
        start_time: parsed.start.toISOString(),
        end_time: parsed.end.toISOString()
      };
    }

    if (filterMode === FILTER_MODE_DAY) {
      const match = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(String(filterDay || ''));
      if (!match) {
        throw new Error('Invalid day filter.');
      }
      const year = Number(match[1]);
      const month = Number(match[2]);
      const day = Number(match[3]);
      const start = new Date(year, month - 1, day, 0, 0, 0, 0);
      const end = new Date(year, month - 1, day + 1, 0, 0, 0, 0);
      return { start_time: start.toISOString(), end_time: end.toISOString() };
    }

    return { start_time: null, end_time: null };
  };

  const applyClientSideFilters = (source) => {
    const rows = Array.isArray(source) ? source : [];
    const filteredByApp = filterApp === FILTER_APP_ALL
      ? rows
      : rows.filter((item) => String(item?.app_name || '').trim() === filterApp);

    if (filterMode !== FILTER_MODE_TIME_OF_DAY) {
      return filteredByApp;
    }
    return filteredByApp.filter((item) => {
      const capturedAt = parseSnapshotDate(item?.captured_at);
      return isInTimeOfDayBucket(capturedAt, filterTimeOfDay);
    });
  };

  const mergeAppFilterOptions = (names) => {
    const normalized = Array.from(
      new Set(
        (Array.isArray(names) ? names : [])
          .map((name) => String(name || '').trim())
          .filter(Boolean)
      )
    ).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));

    appFilterOptions = normalized;
    if (filterApp !== FILTER_APP_ALL && !normalized.includes(filterApp)) {
      filterApp = FILTER_APP_ALL;
    }
  };

  const loadAppFilters = async () => {
    try {
      const payload = await getSnapshotApps();
      mergeAppFilterOptions(payload?.apps || []);
    } catch {
      // API layer tracks this error centrally.
    }
  };

  const revokeImageObjectUrl = (id) => {
    const existing = imageObjectUrlsById.get(id);
    if (existing) {
      URL.revokeObjectURL(existing);
      imageObjectUrlsById.delete(id);
    }
  };

  const pruneImageObjectUrlCache = (activeIds = null) => {
    if (activeIds instanceof Set) {
      for (const [id] of imageObjectUrlsById.entries()) {
        if (!activeIds.has(id)) {
          revokeImageObjectUrl(id);
        }
      }
    }

    while (imageObjectUrlsById.size > IMAGE_OBJECT_URL_CACHE_LIMIT) {
      const oldest = imageObjectUrlsById.keys().next().value;
      if (oldest === undefined) break;
      revokeImageObjectUrl(oldest);
    }
  };

  const revokeAllImageObjectUrls = () => {
    for (const id of Array.from(imageObjectUrlsById.keys())) {
      revokeImageObjectUrl(id);
    }
    imageObjectUrlsById = new Map();
  };

  const loadActiveImage = async (item) => {
    imageLoadToken += 1;
    const token = imageLoadToken;

    const existing = imageObjectUrlsById.get(item?.id);
    if (existing) {
      resolvedImageSrc = existing;
      imageLoading = false;
      return;
    }

    imageLoading = true;

    const src = imageUrl(item);
    if (!src) {
      imageLoading = false;
      return;
    }

    try {
      const res = await fetch(src);
      if (token !== imageLoadToken) return;
      if (res.status === 423) {
        error = 'Snapshot vault is locked. Unlock snapshots first.';
        imageLoading = false;
        return;
      }
      if (!res.ok) {
        throw new Error(`Image request failed (${res.status})`);
      }
      const blob = await res.blob();
      if (token !== imageLoadToken) return;
      const objectUrl = URL.createObjectURL(blob);
      revokeImageObjectUrl(item.id);
      imageObjectUrlsById.set(item.id, objectUrl);
      pruneImageObjectUrlCache();
      resolvedImageSrc = objectUrl;
      if (error === 'Image could not be loaded for this snapshot.') {
        error = '';
      }
    } catch {
      if (token !== imageLoadToken) return;
      error = 'Image could not be loaded for this snapshot.';
      resolvedImageSrc = '';
    } finally {
      if (token === imageLoadToken) {
        imageLoading = false;
      }
    }
  };

  const buildTimeline = (preferredId = null, preferredIndex = null) => {
    const filteredItems = applyClientSideFilters(items);
    timelineItems = [...filteredItems].reverse();
    if (timelineItems.length === 0) {
      scrubIndex = 0;
      scrubDraftIndex = 0;
      return;
    }

    if (preferredId != null) {
      const indexById = timelineItems.findIndex((item) => item?.id === preferredId);
      if (indexById >= 0) {
        scrubIndex = indexById;
        scrubDraftIndex = indexById;
        return;
      }
    }

    if (preferredIndex != null && Number.isFinite(preferredIndex)) {
      const clampedIndex = Math.max(0, Math.min(timelineItems.length - 1, Number(preferredIndex)));
      scrubIndex = clampedIndex;
      scrubDraftIndex = clampedIndex;
      return;
    }

    const newestWithImage = [...timelineItems]
      .map((item, index) => ({ item, index }))
      .reverse()
      .find((entry) => entry.item?.image_available);
    scrubIndex = newestWithImage ? newestWithImage.index : timelineItems.length - 1;
    scrubDraftIndex = scrubIndex;
  };

  const loadSettings = async () => {
    try {
      const settings = await getSnapshotSettings();
      allowDelete = !!settings.allow_individual_delete;
    } catch {
      allowDelete = false;
    }
  };

  let refreshTimer;

  const loadHistory = async (isAutoRefresh) => {
    const auto = isAutoRefresh === true;
    if (!auto) loading = true;
    error = '';

    let range = { start_time: null, end_time: null };
    try {
      range = buildHistoryTimeRange();
    } catch (e) {
      if (!auto) {
        error = e.message || 'Invalid timeline filter';
      }
      loading = false;
      return;
    }

    let isAtRightEdge = false;
    if (timelineItems.length > 0) {
       const newestWithImg = [...timelineItems].map((item, idx) => ({item, idx})).reverse().find(e => e.item?.image_available);
       const rightmostIdx = newestWithImg ? newestWithImg.index : (timelineItems.length - 1);
       if (scrubDraftIndex >= rightmostIdx) isAtRightEdge = true;
    }

    const currentId = activeSnapshot?.id ?? null;
    const currentIndex = scrubIndex;

    try {
      if (!auto) {
        await loadAppFilters();
      }
      const payload = {
        limit: Number(limit) || 200,
        ...(filterApp !== FILTER_APP_ALL ? { app_name: filterApp } : {}),
        ...(range.start_time ? { start_time: range.start_time } : {}),
        ...(range.end_time ? { end_time: range.end_time } : {})
      };
      const data = await getSnapshotHistory(payload);
      items = data.items || [];
      const facetApps = Array.isArray(data?.facets?.apps)
        ? data.facets.apps.map((entry) => {
            if (Array.isArray(entry)) return entry[0];
            if (entry && typeof entry === 'object') return entry.app_name || entry.name;
            return null;
          })
        : [];
      const itemApps = items.map((item) => item?.app_name || null);
      mergeAppFilterOptions([...(appFilterOptions || []), ...facetApps, ...itemApps]);
      pruneImageObjectUrlCache(new Set(items.map((item) => item.id)));

      if (auto && isAtRightEdge) {
        buildTimeline(null, null);
      } else {
        buildTimeline(currentId, currentIndex);
      }
    } catch (e) {
      if (!auto) {
        error = e.message || 'Failed to load snapshot history';
        items = [];
        timelineItems = [];
      }
    } finally {
      loading = false;
    }
  };

  const removeSnapshot = async (snapshotId) => {
    if (!allowDelete) {
      error = 'Individual deletion is currently disabled in Snapshot Settings.';
      return;
    }
    deletingId = snapshotId;
    error = '';
    const currentId = activeSnapshot?.id ?? null;
    const currentIndex = scrubIndex;
    try {
      await deleteSnapshot(snapshotId);
      revokeImageObjectUrl(snapshotId);
      items = items.filter((item) => item.id !== snapshotId);
      const preferredId = currentId !== snapshotId ? currentId : null;
      buildTimeline(preferredId, currentIndex);
    } catch (e) {
      error = e.message || 'Failed to delete snapshot';
    } finally {
      deletingId = null;
    }
  };

  const runAction = async (item) => {
    if (!item?.action?.type || item.action.type === 'none') return;
    busyActionId = item.id;
    error = '';
    try {
      await executeSnapshotAction(item.action.type, item.action.value);
    } catch (e) {
      error = e.message || 'Action failed';
    } finally {
      busyActionId = null;
    }
  };

  const handleScrubStart = () => {
    isScrubbing = true;
  };

  const handleScrubInput = (event) => {
    scrubDraftIndex = Number(event.currentTarget.value);
  };

  const handleScrubEnd = () => {
    isScrubbing = false;
    scrubIndex = scrubDraftIndex;
    if (activeSnapshot?.id) {
      loadActiveImage(activeSnapshot);
    }
  };

  const applyTimelineFilter = async () => {
    await loadHistory();
  };

  const clearTimelineFilter = async () => {
    filterMode = FILTER_MODE_NONE;
    filterApp = FILTER_APP_ALL;
    await loadHistory();
  };

  onMount(async () => {
    await Promise.all([loadSettings(), loadAppFilters(), loadHistory()]);
    refreshTimer = setInterval(() => {
      if (!isScrubbing) {
        loadHistory(true);
      }
    }, 5000);
  });

  onDestroy(() => {
    revokeAllImageObjectUrls();
    if (refreshTimer) clearInterval(refreshTimer);
  });

  $: activeSnapshot = timelineItems[scrubIndex] || null;
  $: timelineLabelSnapshot = timelineItems[scrubDraftIndex] || null;
  $: if (activeSnapshot?.id) {
    if (!isScrubbing) {
      loadActiveImage(activeSnapshot);
    }
  } else {
    resolvedImageSrc = '';
    imageLoading = false;
  }
</script>

<section class="snapshot-history-page">
  <header class="snapshot-head">
    <div>
      <h1>Snapshot History</h1>
      <p class="snapshot-subtitle">Recall-style timeline for recent activity context.</p>
    </div>
    <div class="snapshot-head-meta">
      <span class="badge-soft badge-soft-secondary">
        {#if filterMode === FILTER_MODE_NONE && filterApp === FILTER_APP_ALL}
          {items.length} snapshots
        {:else}
          {timelineItems.length} of {items.length} snapshots
        {/if}
      </span>
      <button class="btn btn-sm btn-outline-secondary refresh-btn" on:click={loadHistory} disabled={loading}>
        Refresh
      </button>
    </div>
  </header>

  <section class="recall-strip">
    <div class="recall-head">
      <h2>Recall Timeline</h2>
      <span class="recall-note">Drag left or right to navigate snapshots.</span>
    </div>

    <div class="timeline-filter-row">
      <div class="timeline-filter-field">
        <label class="form-label fw-semibold" for="snapshot-filter-mode">Filter By Time</label>
        <select id="snapshot-filter-mode" class="form-select" bind:value={filterMode}>
          <option value={FILTER_MODE_NONE}>None</option>
          <option value={FILTER_MODE_YEAR}>Year</option>
          <option value={FILTER_MODE_MONTH}>Month</option>
          <option value={FILTER_MODE_WEEK}>Week</option>
          <option value={FILTER_MODE_DAY}>Day</option>
          <option value={FILTER_MODE_TIME_OF_DAY}>Time of Day</option>
        </select>
      </div>

      {#if filterMode === FILTER_MODE_YEAR}
        <div class="timeline-filter-field">
          <label class="form-label fw-semibold" for="snapshot-filter-year">Year</label>
          <input
            id="snapshot-filter-year"
            class="form-control"
            type="number"
            min="1970"
            max="9999"
            bind:value={filterYear}
          />
        </div>
      {:else if filterMode === FILTER_MODE_MONTH}
        <div class="timeline-filter-field">
          <label class="form-label fw-semibold" for="snapshot-filter-month">Month</label>
          <input id="snapshot-filter-month" class="form-control" type="month" bind:value={filterMonth} />
        </div>
      {:else if filterMode === FILTER_MODE_WEEK}
        <div class="timeline-filter-field">
          <label class="form-label fw-semibold" for="snapshot-filter-week">ISO Week (Mon-Sun)</label>
          <input id="snapshot-filter-week" class="form-control" type="week" bind:value={filterWeek} />
        </div>
      {:else if filterMode === FILTER_MODE_DAY}
        <div class="timeline-filter-field">
          <label class="form-label fw-semibold" for="snapshot-filter-day">Day</label>
          <input id="snapshot-filter-day" class="form-control" type="date" bind:value={filterDay} />
        </div>
      {:else if filterMode === FILTER_MODE_TIME_OF_DAY}
        <div class="timeline-filter-field">
          <label class="form-label fw-semibold" for="snapshot-filter-time">Time of Day</label>
          <select id="snapshot-filter-time" class="form-select" bind:value={filterTimeOfDay}>
            {#each TIME_OF_DAY_BUCKETS as bucket (bucket.value)}
              <option value={bucket.value}>{bucket.label}</option>
            {/each}
          </select>
        </div>
      {/if}

      <div class="timeline-filter-field timeline-filter-app">
        <label class="form-label fw-semibold" for="snapshot-filter-app">Filter By Apps</label>
        <select id="snapshot-filter-app" class="form-select" bind:value={filterApp}>
          <option value={FILTER_APP_ALL}>All Apps</option>
          {#each appFilterOptions as appName (appName)}
            <option value={appName}>{appName}</option>
          {/each}
        </select>
      </div>

      <div class="timeline-filter-actions">
        <button class="btn btn-sm btn-primary" on:click={applyTimelineFilter} disabled={loading}>
          Apply
        </button>
        <button
          class="btn btn-sm btn-outline-secondary"
          on:click={clearTimelineFilter}
          disabled={loading || (filterMode === FILTER_MODE_NONE && filterApp === FILTER_APP_ALL)}
        >
          Clear
        </button>
      </div>
    </div>

    {#if filterMode === FILTER_MODE_TIME_OF_DAY}
      <div class="small text-muted mt-1">Time-of-day filtering uses local timezone buckets.</div>
    {/if}

    {#if error}
      <div class="alert alert-danger mt-2 py-2">{error}</div>
    {/if}
    {#if !allowDelete}
      <div class="small text-muted mt-1">Delete is disabled by default. Enable it in Snapshot Settings if needed.</div>
    {/if}

    {#if timelineItems.length > 0}
      <div class="timeline-caption">
        {timelineLabel(timelineLabelSnapshot)}
      </div>
      <input
        class="form-range recall-slider"
        type="range"
        min="0"
        max={timelineItems.length - 1}
        step="1"
        value={scrubDraftIndex}
        on:input={handleScrubInput}
        on:pointerdown={handleScrubStart}
        on:pointerup={handleScrubEnd}
        on:touchstart={handleScrubStart}
        on:touchend={handleScrubEnd}
        on:change={handleScrubEnd}
        aria-label="Recall timeline scrubber"
      />

      <div class="recall-preview mt-3">
        {#if activeSnapshot}
          <div class="preview-meta mb-2">
            <div class="preview-title">{activeSnapshot.window_title || 'Untitled activity'}</div>
            <div class="preview-subtitle">{activeSnapshot.category || 'Other'} • {activeSnapshot.app_name || 'Unknown app'}</div>
          </div>

          {#if resolvedImageSrc}
            <img
              class="preview-image"
              src={resolvedImageSrc}
              alt="Snapshot preview"
              loading="lazy"
            />
            {#if imageLoading}
              <div class="preview-updating">Updating preview…</div>
            {/if}
          {:else if imageLoading}
            <div class="preview-placeholder">Loading image preview…</div>
          {:else}
            <div class="preview-placeholder">No image available for this snapshot.</div>
          {/if}

          <div class="preview-actions mt-3">
            <button
              class="btn btn-sm btn-outline-primary action-btn"
              on:click={() => runAction(activeSnapshot)}
              disabled={busyActionId === activeSnapshot.id || !activeSnapshot.action || activeSnapshot.action.type === 'none'}
            >
              {busyActionId === activeSnapshot.id ? 'Opening…' : activeSnapshot.action?.label || 'Open'}
            </button>
            <button
              class="btn btn-sm btn-outline-danger action-btn"
              on:click={() => removeSnapshot(activeSnapshot.id)}
              disabled={deletingId === activeSnapshot.id || !allowDelete}
            >
              Delete
            </button>
          </div>
        {/if}
      </div>
    {:else}
      <div class="text-muted small">No timeline snapshots yet. Enable snapshot capture and come back.</div>
    {/if}
  </section>
</section>

<style>
  .snapshot-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 0.8rem;
    flex-wrap: wrap;
    margin-bottom: 1rem;
  }

  .snapshot-head h1 {
    margin: 0;
    font-size: 1.46rem;
    letter-spacing: -0.01em;
    font-weight: 700;
  }

  .snapshot-subtitle {
    margin: 0.24rem 0 0;
    color: var(--text-muted);
  }

  .snapshot-head-meta {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .recall-strip {
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-lg);
    background: var(--surface-elevated);
    box-shadow: var(--shadow-sm);
    padding: 0.85rem 0.95rem;
  }

  .recall-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.7rem;
    flex-wrap: wrap;
    margin-bottom: 0.2rem;
  }

  .recall-head h2 {
    margin: 0;
    font-size: 0.92rem;
    text-transform: none;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    font-weight: 700;
  }

  .recall-note {
    font-size: 0.79rem;
    color: var(--text-muted);
  }

  .timeline-filter-row {
    display: flex;
    flex-wrap: nowrap;
    gap: 0.6rem;
    margin: 0.45rem 0 0.7rem;
    align-items: end;
    overflow-x: auto;
    padding-inline: calc(var(--locus-focus-ring-width) + 2px);
    margin-inline: calc(-1 * (var(--locus-focus-ring-width) + 2px));
  }

  .timeline-filter-field {
    min-width: 0;
    flex: 0 0 190px;
    padding: 2px 0;
  }

  .timeline-filter-field.timeline-filter-app {
    flex-basis: 220px;
  }

  .timeline-filter-field .form-label {
    margin-bottom: 0.25rem;
    font-size: 0.78rem;
  }

  .timeline-filter-actions {
    display: flex;
    align-items: end;
    gap: 0.4rem;
    flex-wrap: nowrap;
    justify-content: flex-start;
    margin-left: auto;
    flex: 0 0 auto;
  }

  .timeline-caption {
    margin-bottom: 0.45rem;
    font-size: 0.78rem;
    color: var(--text-muted);
  }

  .recall-slider {
    margin-top: 0.2rem;
  }

  .recall-preview {
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: 0.82rem;
    background: var(--surface-soft);
  }

  .preview-title {
    font-size: 0.92rem;
    font-weight: 600;
    color: var(--text-primary);
    line-height: 1.3;
  }

  .preview-subtitle {
    color: var(--text-muted);
    font-size: 0.79rem;
  }

  .preview-image {
    width: 100%;
    max-height: 380px;
    object-fit: contain;
    border-radius: 0.6rem;
    border: 1px solid var(--border-subtle);
    background: color-mix(in srgb, var(--surface-soft) 68%, #0b1120);
  }

  .preview-placeholder {
    min-height: 120px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px dashed color-mix(in srgb, var(--border-strong) 72%, transparent);
    border-radius: 0.6rem;
    color: var(--text-muted);
    font-size: 0.85rem;
    background: var(--surface-elevated);
  }

  .preview-updating {
    margin-top: 0.45rem;
    font-size: 0.75rem;
    color: var(--text-muted);
  }

  .refresh-btn {
    min-width: 96px;
  }

  .preview-actions {
    display: flex;
    gap: 0.45rem;
    flex-wrap: wrap;
  }

  .action-btn {
    min-width: 98px;
    justify-content: center;
  }

  :global(.theme-dark) .recall-preview {
    background: var(--surface-soft);
    border-color: var(--border-subtle);
  }

  :global(.theme-dark) .preview-placeholder {
    color: var(--text-muted);
    background: var(--surface-elevated);
    border-color: color-mix(in srgb, var(--border-strong) 74%, transparent);
  }

  :global(.theme-dark) .preview-updating {
    color: var(--text-muted);
  }

  @media (max-width: 720px) {
    .recall-strip {
      padding: 0.75rem 0.78rem;
    }

    .timeline-filter-row {
      flex-wrap: wrap;
      overflow: visible;
    }

    .timeline-filter-field {
      flex: 1 1 160px;
    }

    .timeline-filter-field.timeline-filter-app {
      flex-basis: 100%;
    }

    .timeline-filter-actions {
      margin-left: 0;
      width: 100%;
    }

    .preview-actions {
      width: 100%;
    }

    .action-btn {
      flex: 1 1 120px;
    }
  }
</style>
