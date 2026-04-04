<script>
  import { onDestroy, onMount } from 'svelte';
  import {
    BASE_URL,
    deleteSnapshot,
    executeSnapshotAction,
    getSnapshotHistory,
    getSnapshotSettings
  } from '../api.js';

  let loading = false;
  let busyActionId = null;
  let deletingId = null;
  let error = '';

  let limit = 400;

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
    if (!value) return '';
    // Ensure naive UTC strings are treated as UTC by the Date constructor
    let normalized = value;
    if (typeof value === 'string' && !value.endsWith('Z') && !value.includes('+') && !value.includes('-')) {
      normalized = value.replace(' ', 'T') + 'Z';
    }
    const date = new Date(normalized);
    return new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    }).format(date);
  };

  const timelineLabel = (item) => {
    if (!item) return 'No snapshot';
    return `${formatTime(item.captured_at)} - ${item.app_name || 'Unknown app'}`;
  };

  const imageUrl = (item) => {
    if (!item?.image_available || !item?.image_endpoint) return null;
    return `${BASE_URL}${item.image_endpoint}`;
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
    timelineItems = [...items].reverse();
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
    
    let isAtRightEdge = false;
    if (timelineItems.length > 0) {
       const newestWithImg = [...timelineItems].map((item, idx) => ({item, idx})).reverse().find(e => e.item?.image_available);
       const rightmostIdx = newestWithImg ? newestWithImg.index : (timelineItems.length - 1);
       if (scrubDraftIndex >= rightmostIdx) isAtRightEdge = true;
    }

    const currentId = activeSnapshot?.id ?? null;
    const currentIndex = scrubIndex;

    try {
      const data = await getSnapshotHistory({
        limit: Number(limit) || 200
      });
      items = data.items || [];
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

  onMount(async () => {
    await Promise.all([loadSettings(), loadHistory()]);
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
      <span class="badge-soft badge-soft-secondary">{items.length} snapshots</span>
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

    .preview-actions {
      width: 100%;
    }

    .action-btn {
      flex: 1 1 120px;
    }
  }
</style>
