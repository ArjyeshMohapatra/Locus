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
  let imageObjectUrlsById = {};
  let imageLoadToken = 0;
  let isScrubbing = false;

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

  const revokeAllImageObjectUrls = () => {
    Object.values(imageObjectUrlsById).forEach((url) => {
      if (url) URL.revokeObjectURL(url);
    });
    imageObjectUrlsById = {};
  };

  const loadActiveImage = async (item) => {
    imageLoadToken += 1;
    const token = imageLoadToken;

    const existing = imageObjectUrlsById[item?.id];
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
      imageObjectUrlsById = { ...imageObjectUrlsById, [item.id]: objectUrl };
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
  <header class="d-flex justify-content-between align-items-start mb-4 flex-wrap gap-3">
    <div>
      <h1 class="fw-bold mb-1">Snapshot History</h1>
      <p class="text-muted mb-0">Recall-style timeline: drag the pointer to move through what you were doing.</p>
    </div>
    <div class="d-flex align-items-center gap-2">
      <span class="badge-soft badge-soft-secondary">{items.length} snapshots</span>
      <button class="btn btn-sm btn-outline-secondary refresh-btn" on:click={loadHistory} disabled={loading}>
        Refresh
      </button>
    </div>
  </header>

  <section class="card border-0 rounded-4 shadow-sm p-3 mb-4 recall-strip">
    <div class="d-flex justify-content-between align-items-center flex-wrap gap-2 mb-2">
      <h5 class="mb-0 fw-semibold">Recall Timeline</h5>
      <span class="small text-muted">Drag pointer left/right to travel through time</span>
    </div>

    {#if error}
      <div class="alert alert-danger mt-2 py-2">{error}</div>
    {/if}
    {#if !allowDelete}
      <div class="small text-muted mt-1">Delete is disabled by default. Enable it in Snapshot Settings if needed.</div>
    {/if}

    {#if timelineItems.length > 0}
      <div class="mb-2 small text-muted">
        {timelineLabel(timelineLabelSnapshot)}
      </div>
      <input
        class="form-range"
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
            <div class="fw-semibold">{activeSnapshot.window_title || 'Untitled activity'}</div>
            <div class="small text-muted">{activeSnapshot.category || 'Other'} • {activeSnapshot.app_name || 'Unknown app'}</div>
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

          <div class="mt-3 d-flex gap-2 flex-wrap">
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
  .recall-strip {
    background: var(--surface-elevated, #fff);
  }

  .recall-preview {
    border: 1px solid rgba(0, 0, 0, 0.08);
    border-radius: 0.85rem;
    padding: 0.85rem;
    background: rgba(248, 250, 252, 0.9);
  }

  .preview-meta {
    color: var(--text-primary);
  }

  .preview-meta .fw-semibold {
    color: var(--text-primary);
    line-height: 1.3;
  }

  .preview-image {
    width: 100%;
    max-height: 380px;
    object-fit: contain;
    border-radius: 0.6rem;
    border: 1px solid rgba(0, 0, 0, 0.08);
    background: #0b1120;
  }

  .preview-placeholder {
    min-height: 120px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px dashed rgba(100, 116, 139, 0.5);
    border-radius: 0.6rem;
    color: #64748b;
    font-size: 0.9rem;
    background: rgba(255, 255, 255, 0.65);
  }

  .preview-updating {
    margin-top: 0.45rem;
    font-size: 0.75rem;
    color: var(--text-muted);
  }

  .refresh-btn {
    min-width: 108px;
  }

  .action-btn {
    min-width: 112px;
    justify-content: center;
  }

  :global(.theme-dark) .recall-preview {
    background: rgba(15, 23, 42, 0.75);
    border-color: rgba(148, 163, 184, 0.25);
  }

  :global(.theme-dark) .preview-meta,
  :global(.theme-dark) .preview-meta .fw-semibold {
    color: #e2e8f0;
  }

  :global(.theme-dark) .preview-placeholder {
    color: #94a3b8;
    background: rgba(2, 6, 23, 0.6);
    border-color: rgba(148, 163, 184, 0.35);
  }

  :global(.theme-dark) .preview-updating {
    color: #94a3b8;
  }
</style>
