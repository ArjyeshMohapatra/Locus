<script>
  import { onMount, onDestroy } from 'svelte';
  import { slide } from 'svelte/transition';
  import { getRecentFileEvents, getWatchedTree, subscribeFileEvents } from '../api.js';
  import { addErrorMessage } from '../errorStore.js';
  import Fa from 'svelte-fa';
  import { faChevronRight } from '@fortawesome/free-solid-svg-icons';
  import FileHistoryModal from './FileHistoryModal.svelte';

  let events = [];
  let watchedTree = [];
  let fileEventsByPath = {};
  let eventSource;
  let treeRefreshTimer = null;
  let periodicRefreshTimer = null;
  let refreshInFlight = false;
  let selectedFile = null;
  let expandedRoots = new Set();
  let expandedDirs = new Set();
  let expandedFiles = new Set();
  let lastStreamEventAt = 0;

  const NEW_WINDOW_MS = 60 * 1000;
  const POLL_REFRESH_MS = 20000;
  const STREAM_STALE_MS = 15000;

  const setDocumentModalLock = (locked) => {
    if (typeof document === 'undefined') return;
    document.body.style.overflow = locked ? 'hidden' : '';
    document.body.style.overflowX = locked ? 'hidden' : '';
    document.body.style.overscrollBehaviorX = locked ? 'none' : '';
    document.documentElement.style.overflow = locked ? 'hidden' : '';
    document.documentElement.style.overflowX = locked ? 'hidden' : '';
    document.documentElement.style.overscrollBehaviorX = locked ? 'none' : '';
  };

  $: if (typeof document !== 'undefined') {
    setDocumentModalLock(Boolean(selectedFile));
  }

  const normalizePath = (path) => String(path || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();

  const isEventUnderRoot = (eventPath, rootPath) => {
    const normalizedEvent = normalizePath(eventPath);
    const normalizedRoot = normalizePath(rootPath);
    if (!normalizedEvent || !normalizedRoot) return false;
    return normalizedEvent === normalizedRoot || normalizedEvent.startsWith(`${normalizedRoot}/`);
  };

  $: sortedRoots = [...watchedTree].sort((a, b) => {
    const nameA = a?.tree?.name || a?.path || '';
    const nameB = b?.tree?.name || b?.path || '';
    return nameA.localeCompare(nameB);
  });

  $: eventsByFile = events.reduce((acc, event) => {
    const filePath = event?.src_path;
    if (!filePath) return acc;
    const key = normalizePath(filePath);
    if (!acc[key]) {
      acc[key] = [];
    }
    acc[key].push(event);
    return acc;
  }, {});

  const getRootLatestTimestamp = (rootPath) => {
    let latest = null;
    for (const event of events) {
      if (!isEventUnderRoot(event?.src_path, rootPath)) continue;
      if (!latest || new Date(event.timestamp) > new Date(latest)) {
        latest = event.timestamp;
      }
    }
    return latest;
  };

  const getEventsForFilePath = (filePath) => {
    const key = normalizePath(filePath);
    return fileEventsByPath[key] || eventsByFile[key] || [];
  };

  const flattenTreeRows = (treeNode, depth = 0) => {
    const rows = [];
    const children = Array.isArray(treeNode?.children) ? treeNode.children : [];

    for (const child of children) {
      if (child.type === 'dir') {
        const dirKey = `dir:${normalizePath(child.path)}`;
        rows.push({ type: 'dir', key: dirKey, node: child, depth });

        if (expandedDirs.has(dirKey)) {
          const childRows = flattenTreeRows(child, depth + 1);
          if (childRows.length === 0) {
            rows.push({ type: 'dir-empty', key: `dir-empty:${normalizePath(child.path)}`, depth: depth + 1 });
          } else {
            rows.push(...childRows);
          }
        }
      } else if (child.type === 'file') {
        const fileEvents = getEventsForFilePath(child.path);
        rows.push({ type: 'file', key: `file:${normalizePath(child.path)}`, node: child, events: fileEvents, depth });

        if (expandedFiles.has(child.path)) {
          if (fileEvents.length === 0) {
            rows.push({
              type: 'file-empty',
              key: `file-empty:${normalizePath(child.path)}`,
              depth: depth + 1
            });
          } else {
            fileEvents.forEach((event, index) => {
              rows.push({
                type: 'event',
                key: `event:${normalizePath(child.path)}:${index}`,
                event,
                depth: depth + 1
              });
            });
          }
        }
      }
    }

    return rows;
  };

  // Reference all dependencies explicitly so Svelte's reactivity tracks them.
  // flattenTreeRows reads expandedDirs, expandedFiles, fileEventsByPath & eventsByFile
  // internally, but Svelte can't see inside function bodies.
  $: visibleRowsByRoot = (() => {
    // Touch dependencies so Svelte re-runs this block when they change
    expandedDirs;
    expandedFiles;
    fileEventsByPath;
    eventsByFile;
    return Object.fromEntries(
      sortedRoots.map((root) => [root.path, flattenTreeRows(root.tree, 0)])
    );
  })();

  async function refresh({ resetFileCache = false } = {}) {
    if (refreshInFlight) return;
    refreshInFlight = true;
    try {
      const [eventList, treeList] = await Promise.all([
        getRecentFileEvents(50),
        getWatchedTree()
      ]);
      events = Array.isArray(eventList) ? eventList : [];
      watchedTree = Array.isArray(treeList) ? treeList : [];
      if (resetFileCache) {
        fileEventsByPath = {};
      }
    } catch (e) {
      console.error(e);
    } finally {
      refreshInFlight = false;
    }
  }

  async function loadFileEvents(filePath) {
    try {
      const key = normalizePath(filePath);
      const eventList = await getRecentFileEvents(500, filePath);
      fileEventsByPath = {
        ...fileEventsByPath,
        [key]: Array.isArray(eventList) ? eventList : []
      };
    } catch (e) {
      console.error(e);
    }
  }

  function scheduleTreeRefresh() {
    if (treeRefreshTimer) {
      clearTimeout(treeRefreshTimer);
    }
    treeRefreshTimer = setTimeout(async () => {
      try {
        watchedTree = await getWatchedTree();
      } catch (e) {
        console.error(e);
      }
    }, 250);
  }

  function toggleRoot(rootPath) {
    const newSet = new Set(expandedRoots);
    if (newSet.has(rootPath)) {
      newSet.delete(rootPath);
    } else {
      newSet.add(rootPath);
    }
    expandedRoots = newSet;
  }

  function toggleDir(dirKey) {
    const newSet = new Set(expandedDirs);
    if (newSet.has(dirKey)) {
      newSet.delete(dirKey);
    } else {
      newSet.add(dirKey);
    }
    expandedDirs = newSet;
  }

  function toggleFile(filePath) {
    const newSet = new Set(expandedFiles);
    const willOpen = !newSet.has(filePath);
    if (!willOpen) {
      newSet.delete(filePath);
    } else {
      newSet.add(filePath);
      loadFileEvents(filePath);
    }
    expandedFiles = newSet;
  }

  function isNewEvent(timestamp) {
    const time = new Date(timestamp + (timestamp.includes('Z') ? '' : 'Z')).getTime();
    return Date.now() - time <= NEW_WINDOW_MS;
  }

  function formatTime(timestamp) {
    const date = new Date(timestamp + (timestamp.includes('Z') ? '' : 'Z'));
    return new Intl.DateTimeFormat(undefined, {
      weekday: 'short',
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true
    }).format(date);
  }

  onMount(() => {
    refresh({ resetFileCache: true });
    lastStreamEventAt = Date.now();
    periodicRefreshTimer = setInterval(() => {
      if (!eventSource || Date.now() - lastStreamEventAt > STREAM_STALE_MS) {
        refresh();
      }
    }, POLL_REFRESH_MS);

    eventSource = subscribeFileEvents((event) => {
      lastStreamEventAt = Date.now();
      if (event?.event_type) {
        events = [event, ...events].slice(0, 500);
        if (event?.src_path) {
          const key = normalizePath(event.src_path);
          const existing = fileEventsByPath[key] || [];
          const deduped = [event, ...existing.filter((item) => item.id !== event.id)].slice(0, 500);
          fileEventsByPath = { ...fileEventsByPath, [key]: deduped };
        }
        scheduleTreeRefresh();
      }
      if (event?.type === 'snapshot_error') {
        addErrorMessage(event.message || 'Snapshot error occurred.');
      }
    });
  });

  onDestroy(() => {
    if (eventSource) {
      eventSource.close();
    }
    if (treeRefreshTimer) {
      clearTimeout(treeRefreshTimer);
      treeRefreshTimer = null;
    }
    if (periodicRefreshTimer) {
      clearInterval(periodicRefreshTimer);
      periodicRefreshTimer = null;
    }
    setDocumentModalLock(false);
  });
</script>

<section class="activity-card">
  <header class="activity-card-head">
    <h2>Live Activity</h2>
    <span class="badge-soft badge-soft-secondary">{sortedRoots.length} watched</span>
  </header>

  <div class="activity-list">
    {#each sortedRoots as root}
      {@const rootPath = root.path}
      {@const rootRows = visibleRowsByRoot[rootPath] || []}
      {@const rootLatest = getRootLatestTimestamp(rootPath)}

      <article class="activity-item activity-root {expandedRoots.has(rootPath) ? 'is-expanded' : ''}">
        <div class="activity-row-main">
          <button
            class="activity-row-btn"
            on:click={() => toggleRoot(rootPath)}
            type="button"
          >
            <span class="section-chevron activity-chevron {expandedRoots.has(rootPath) ? 'rotated' : ''}">
              <Fa icon={faChevronRight} aria-hidden="true" />
            </span>
            <div class="activity-title-wrap">
              <span class="activity-title">{root.tree?.name || rootPath}</span>
              <small class="activity-path text-truncate">{rootPath}</small>
            </div>
          </button>

          <div class="activity-row-meta">
            {#if rootLatest && isNewEvent(rootLatest)}
              <span class="badge-soft badge-soft-success">NEW</span>
            {/if}
            <span class="badge-soft badge-soft-secondary badge-stack">{root.tree?.file_count || 0}</span>
          </div>
        </div>

        {#if expandedRoots.has(rootPath)}
          <div class="event-list" transition:slide={{ duration: 200 }}>
            {#if rootRows.length === 0}
              <div class="tree-empty" style="--tree-indent: 10px;">No files found in this watched folder.</div>
            {:else}
              {#each rootRows as row (row.key)}
                {#if row.type === 'dir'}
                  <div class="activity-item activity-child-row" style="--tree-indent: {12 + row.depth * 18}px;">
                    <div class="activity-row-main">
                      <button
                        class="activity-row-btn"
                        on:click={() => toggleDir(row.key)}
                        type="button"
                      >
                        <span class="section-chevron activity-chevron {expandedDirs.has(row.key) ? 'rotated' : ''}">
                          <Fa icon={faChevronRight} aria-hidden="true" />
                        </span>
                        <span class="activity-title">{row.node.name}</span>
                      </button>
                      <span class="badge-soft badge-soft-secondary badge-stack">{row.node.file_count || 0}</span>
                    </div>
                  </div>
                {:else if row.type === 'file'}
                  <div class="activity-item activity-child-row {expandedFiles.has(row.node.path) ? 'is-expanded' : ''}" style="--tree-indent: {12 + row.depth * 18}px;">
                    <div class="activity-row-main">
                      <button
                        class="activity-row-btn"
                        on:click={() => toggleFile(row.node.path)}
                        type="button"
                      >
                        <span class="section-chevron activity-chevron {expandedFiles.has(row.node.path) ? 'rotated' : ''}">
                          <Fa icon={faChevronRight} aria-hidden="true" />
                        </span>
                        <div class="activity-title-wrap">
                          <span class="activity-title">{row.node.name}</span>
                          <small class="activity-path text-truncate">{row.node.path}</small>
                        </div>
                      </button>

                      <div class="activity-row-meta">
                        <button
                          class="btn btn-sm btn-outline-primary activity-history-btn"
                          on:click={(e) => { e.stopPropagation(); selectedFile = row.node.path; }}
                          title="View History / Restore"
                        >
                          History
                        </button>
                        <span class="badge-soft badge-soft-secondary badge-stack">{row.events.length}</span>
                      </div>
                    </div>
                  </div>
                {:else if row.type === 'event'}
                  <div class="event-row" style="--tree-indent: {12 + row.depth * 18}px;">
                    <span class="badge-soft badge-soft-secondary event-kind">{row.event.event_type}</span>
                    <span class="activity-time">{formatTime(row.event.timestamp)}</span>
                    {#if row.event.dest_path}
                      <small class="activity-path text-truncate">&rarr; {row.event.dest_path.split(/[\\/]/).pop()}</small>
                    {/if}
                  </div>
                {:else if row.type === 'dir-empty'}
                  <div class="tree-empty" style="--tree-indent: {12 + row.depth * 18}px;">
                    No visible items in this folder.
                  </div>
                {:else if row.type === 'file-empty'}
                  <div class="tree-empty" style="--tree-indent: {12 + row.depth * 18}px;">
                    No tracked events yet for this file.
                  </div>
                {/if}
              {/each}
            {/if}
          </div>
        {/if}
      </article>
    {/each}

    {#if sortedRoots.length === 0}
      <div class="activity-empty">
        <em>No watched folders found</em>
      </div>
    {/if}
  </div>
</section>

<FileHistoryModal filePath={selectedFile} onClose={() => selectedFile = null} />

<style>
  .activity-card {
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-lg);
    background: var(--surface-elevated);
    box-shadow: var(--shadow-sm);
    max-height: min(74vh, calc(100vh - 190px));
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .activity-card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
    padding: 0.82rem 0.95rem;
    border-bottom: 1px solid var(--border-subtle);
    background: var(--surface-soft);
  }

  .activity-card-head h2 {
    margin: 0;
    font-size: 0.92rem;
    letter-spacing: 0.05em;
    text-transform: none;
    color: var(--text-muted);
    font-weight: 700;
  }

  .activity-list {
    max-height: min(66vh, calc(100vh - 250px));
    overflow: auto;
    padding: 0.3rem 0;
  }

  .activity-item {
    border-bottom: 1px solid var(--border-subtle);
    padding: 0.15rem 0.62rem;
  }

  .activity-item:last-child {
    border-bottom: none;
  }

  .activity-root {
    padding-top: 0.34rem;
    padding-bottom: 0.34rem;
  }

  .activity-child-row {
    padding-left: var(--tree-indent);
  }

  .activity-row-main {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }

  .activity-row-btn {
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    padding: 0.32rem 0;
    display: flex;
    align-items: center;
    gap: 0.55rem;
    text-align: left;
    color: inherit;
  }

  .activity-chevron {
    color: var(--text-muted);
    margin: 0;
  }

  .activity-title-wrap {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
  }

  .activity-title {
    font-size: 0.84rem;
    font-weight: 600;
    color: var(--text-primary);
    line-height: 1.25;
  }

  .activity-row-meta {
    display: flex;
    align-items: center;
    gap: 0.45rem;
  }

  .activity-path {
    font-size: 0.71rem;
    max-width: 340px;
    line-height: 1.2;
    color: var(--text-muted);
  }

  .activity-history-btn {
    min-width: 86px;
  }

  .activity-time {
    font-size: 0.71rem;
    color: var(--text-muted);
    margin-right: auto;
  }

  .event-list {
    margin: 0.12rem 0 0.42rem;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    background: var(--surface-soft);
    overflow: hidden;
  }

  .event-row {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.42rem 0.58rem 0.42rem var(--tree-indent);
    border-bottom: 1px solid var(--border-subtle);
  }

  .event-row:last-child {
    border-bottom: none;
  }

  .event-kind {
    min-width: 80px;
    justify-content: center;
    text-transform: none;
    font-size: 0.66rem;
    letter-spacing: 0.04em;
  }

  .tree-empty {
    padding: 0.34rem 0.55rem 0.42rem var(--tree-indent);
    font-size: 0.76rem;
    color: var(--text-muted);
  }

  .activity-empty {
    text-align: center;
    color: var(--text-muted);
    padding: 1.1rem 0.8rem;
  }

  .badge-stack {
    min-width: 38px;
    display: inline-flex;
    justify-content: center;
  }

  @media (max-width: 720px) {
    .activity-card-head {
      padding: 0.74rem 0.82rem;
    }

    .activity-item,
    .activity-root {
      padding-left: 0.5rem;
      padding-right: 0.5rem;
    }

    .activity-history-btn {
      min-width: 74px;
    }

    .activity-path {
      max-width: 220px;
    }
  }
</style>
