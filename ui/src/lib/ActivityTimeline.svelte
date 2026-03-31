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

  const NEW_WINDOW_MS = 60 * 1000;
  const POLL_REFRESH_MS = 5000;

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
    periodicRefreshTimer = setInterval(() => {
      refresh();
    }, POLL_REFRESH_MS);

    eventSource = subscribeFileEvents((event) => {
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

<div class="card rounded-4 activity-card">
  <div class="card-header d-flex align-items-center justify-content-between py-3 px-4">
    <h5 class="card-title mb-0 fw-bold">Live Activity</h5>
    <span class="badge-soft badge-soft-secondary">{sortedRoots.length} Watched</span>
  </div>
  <div class="card-body p-0">
    <div class="activity-list overflow-auto">
      {#each sortedRoots as root}
        {@const rootPath = root.path}
        {@const rootRows = visibleRowsByRoot[rootPath] || []}
        {@const rootLatest = getRootLatestTimestamp(rootPath)}

        <div class="activity-item {expandedRoots.has(rootPath) ? 'is-expanded' : ''}">
          <div class="d-flex justify-content-between align-items-center w-100">
            <button
              class="d-flex align-items-center flex-grow-1 text-start border-0 bg-transparent p-0"
              on:click={() => toggleRoot(rootPath)}
              type="button"
            >
              <div class="d-flex align-items-center">
                <span class="section-chevron me-3 {expandedRoots.has(rootPath) ? 'rotated' : ''}">
                  <Fa icon={faChevronRight} aria-hidden="true" />
                </span>
                <div class="text-truncate">
                  <span class="activity-details d-block fw-semibold" style="color: var(--text-primary);">
                    {root.tree?.name || rootPath}
                  </span>
                  <small class="activity-path text-muted d-block text-truncate">
                    {rootPath}
                  </small>
                </div>
              </div>
            </button>

            <div class="d-flex align-items-center ms-2 gap-2">
              {#if rootLatest && isNewEvent(rootLatest)}
                <span class="badge-soft badge-soft-success">NEW</span>
              {/if}
              <span class="badge-soft badge-soft-secondary badge-stack">{root.tree?.file_count || 0}</span>
            </div>
          </div>

          {#if expandedRoots.has(rootPath)}
            <div class="event-list mt-2" transition:slide={{ duration: 200 }}>
              {#if rootRows.length === 0}
                <div class="text-muted small py-2 px-2">No files found in this watched folder.</div>
              {:else}
                {#each rootRows as row (row.key)}
                  {#if row.type === 'dir'}
                    <div class="activity-item" style="padding: 8px 0 8px {12 + row.depth * 18}px;">
                      <div class="d-flex justify-content-between align-items-center w-100">
                        <button
                          class="d-flex align-items-center flex-grow-1 text-start border-0 bg-transparent p-0"
                          on:click={() => toggleDir(row.key)}
                          type="button"
                        >
                          <span class="section-chevron me-3 {expandedDirs.has(row.key) ? 'rotated' : ''}">
                            <Fa icon={faChevronRight} aria-hidden="true" />
                          </span>
                          <span class="activity-details d-block fw-semibold" style="color: var(--text-primary);">
                            {row.node.name}
                          </span>
                        </button>
                        <span class="badge-soft badge-soft-secondary badge-stack">{row.node.file_count || 0}</span>
                      </div>
                    </div>
                  {:else if row.type === 'file'}
                    <div class="activity-item {expandedFiles.has(row.node.path) ? 'is-expanded' : ''}" style="padding: 8px 0 8px {12 + row.depth * 18}px;">
                      <div class="d-flex justify-content-between align-items-center w-100">
                        <button
                          class="d-flex align-items-center flex-grow-1 text-start border-0 bg-transparent p-0"
                          on:click={() => toggleFile(row.node.path)}
                          type="button"
                        >
                          <span class="section-chevron me-3 {expandedFiles.has(row.node.path) ? 'rotated' : ''}">
                            <Fa icon={faChevronRight} aria-hidden="true" />
                          </span>
                          <div class="text-truncate">
                            <span class="activity-details d-block fw-semibold" style="color: var(--text-primary);">
                              {row.node.name}
                            </span>
                            <small class="activity-path text-muted d-block text-truncate">{row.node.path}</small>
                          </div>
                        </button>

                        <div class="d-flex align-items-center ms-2">
                          <button
                            class="btn btn-sm btn-outline-primary me-3"
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
                    <div class="event-row" style="margin-left: {12 + row.depth * 18}px;">
                      <span class="badge-soft badge-soft-secondary me-3" style="min-width: 80px; justify-content: center;">{row.event.event_type}</span>
                      <span class="activity-time me-auto">{formatTime(row.event.timestamp)}</span>
                      {#if row.event.dest_path}
                        <small class="activity-path text-muted ms-2 text-truncate">&rarr; {row.event.dest_path.split(/[\\/]/).pop()}</small>
                      {/if}
                    </div>
                  {:else if row.type === 'dir-empty'}
                    <div class="text-muted small" style="padding: 4px 0 8px {12 + row.depth * 18}px;">
                      No visible items in this folder.
                    </div>
                  {:else if row.type === 'file-empty'}
                    <div class="text-muted small" style="padding: 4px 0 8px {12 + row.depth * 18}px;">
                      No tracked events yet for this file.
                    </div>
                  {/if}
                {/each}
              {/if}
            </div>
          {/if}
        </div>
      {/each}

      {#if sortedRoots.length === 0}
        <div class="text-center text-muted py-4">
          <em>No watched folders found</em>
        </div>
      {/if}
    </div>
  </div>
</div>

<FileHistoryModal filePath={selectedFile} onClose={() => selectedFile = null} />

<style>
  .activity-card {
    max-height: min(72vh, calc(100vh - 200px));
    overflow: hidden;
  }

  .activity-list {
    max-height: min(64vh, calc(100vh - 260px));
    overflow: auto;
  }

  .activity-path {
    font-size: 0.7rem;
    max-width: 220px;
    line-height: 1.2;
    color: var(--text-muted);
  }

  .activity-path::after {
    content: '';
  }

  .activity-time {
    font-size: 0.72rem;
  }

  .badge-stack {
    margin-right: 10px;
    min-width: 40px;
    display: inline-flex;
    justify-content: center;
  }
</style>
