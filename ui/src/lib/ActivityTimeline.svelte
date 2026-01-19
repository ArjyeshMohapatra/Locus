<script>
  import { onMount, onDestroy } from 'svelte';
  import { slide } from 'svelte/transition';
  import { getRecentFileEvents, subscribeFileEvents } from '../api.js';
  import { addErrorMessage } from '../errorStore.js';
  import Fa from 'svelte-fa';
  import { faChevronDown, faChevronRight } from '@fortawesome/free-solid-svg-icons';
  import FileHistoryModal from './FileHistoryModal.svelte';

  let events = [];
  let eventSource;
  let selectedFile = null;
  let expandedFiles = new Set();
  let expandedDirs = new Set();

  const NEW_WINDOW_MS = 60 * 1000;
  
  $: if (typeof document !== 'undefined') {
    const lock = Boolean(selectedFile);
    document.body.style.overflow = lock ? 'hidden' : '';
    document.body.style.overflowX = lock ? 'hidden' : '';
    document.body.style.overscrollBehaviorX = lock ? 'none' : '';
    document.documentElement.style.overflow = lock ? 'hidden' : '';
    document.documentElement.style.overflowX = lock ? 'hidden' : '';
    document.documentElement.style.overscrollBehaviorX = lock ? 'none' : '';
  }
  
  const getDirPath = (path) => {
    if (!path) return 'Unknown';
    const cleaned = path.replace(/[\\/]+$/, '');
    const parts = cleaned.split(/[\\/]/);
    parts.pop();
    return parts.join('/') || cleaned;
  };

  // Grouped events: { "dir": { files: { "path/to/file": [events] }, latest: event } }
  $: groupedByDir = events.reduce((acc, event) => {
    const filePath = event.src_path;
    const dirPath = getDirPath(filePath);
    if (!acc[dirPath]) {
      acc[dirPath] = { files: {}, latest: event };
    }
    if (!acc[dirPath].files[filePath]) {
      acc[dirPath].files[filePath] = [];
    }
    acc[dirPath].files[filePath].push(event);
    if (!acc[dirPath].latest || new Date(event.timestamp) > new Date(acc[dirPath].latest.timestamp)) {
      acc[dirPath].latest = event;
    }
    return acc;
  }, {});

  $: sortedDirs = Object.keys(groupedByDir).sort((a, b) => {
    const timeA = new Date(groupedByDir[a].latest.timestamp);
    const timeB = new Date(groupedByDir[b].latest.timestamp);
    return timeB - timeA;
  });

  async function refresh() {
    try {
      events = await getRecentFileEvents(50);
    } catch (e) {
      console.error(e);
    }
  }

  function toggleExpand(path) {
    const newSet = new Set(expandedFiles);
    if (newSet.has(path)) {
      newSet.delete(path);
    } else {
      newSet.add(path);
    }
    expandedFiles = newSet;
  }

  function toggleDir(dirPath) {
    const newSet = new Set(expandedDirs);
    if (newSet.has(dirPath)) {
      newSet.delete(dirPath);
    } else {
      newSet.add(dirPath);
    }
    expandedDirs = newSet;
  }

  function isNewEvent(timestamp) {
    const time = new Date(timestamp + (timestamp.includes('Z') ? '' : 'Z')).getTime();
    return Date.now() - time <= NEW_WINDOW_MS;
  }

  function formatTime(timestamp) {
    // Ensure timestamp is treated as UTC
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
    refresh();
    eventSource = subscribeFileEvents((event) => {
      if (event?.event_type) {
        events = [event, ...events].slice(0, 50);
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
  });
</script>

<div class="card h-100 rounded-4">
  <div class="card-header d-flex align-items-center justify-content-between py-3 px-4">
    <h5 class="card-title mb-0 fw-bold">Live File Activity</h5>
    <span class="badge-soft badge-soft-secondary">{sortedDirs.length} Directories</span>
  </div>
  <div class="card-body p-0">
    <div class="activity-list overflow-auto" style="height: 350px;">
      {#each sortedDirs as dirPath}
        {@const dirGroup = groupedByDir[dirPath]}
        {@const filesInDir = Object.keys(dirGroup.files)}
        {@const dirIsNew = dirGroup.latest ? isNewEvent(dirGroup.latest.timestamp) : false}

        <div class="activity-item {expandedDirs.has(dirPath) ? 'is-expanded' : ''}">
          <!-- Directory Header -->
          <div class="d-flex justify-content-between align-items-center w-100">
            <button
              class="d-flex align-items-center flex-grow-1 text-start border-0 bg-transparent p-0"
              on:click={() => toggleDir(dirPath)}
              type="button"
            >
              <div class="d-flex align-items-center">
                <span class="section-chevron me-3 {expandedDirs.has(dirPath) ? 'rotated' : ''}">
                  <Fa icon={faChevronRight} aria-hidden="true" />
                </span>
                <div class="text-truncate">
                  <span class="activity-details d-block fw-semibold" style="color: var(--text-primary);">
                    {dirPath.split(/[\\/]/).pop() || dirPath}
                  </span>
                  <small class="activity-path text-muted d-block text-truncate">
                    {dirPath}
                  </small>
                </div>
              </div>
            </button>

            <div class="d-flex align-items-center ms-2 gap-2">
              {#if dirIsNew}
                <span class="badge-soft badge-soft-success">NEW</span>
              {/if}
                <span class="badge-soft badge-soft-secondary badge-stack">{filesInDir.length}</span>
            </div>
          </div>

          {#if expandedDirs.has(dirPath)}
            <div class="event-list mt-2" transition:slide={{ duration: 300 }}>
              {#each filesInDir as filePath}
                {@const fileEvents = dirGroup.files[filePath]}
                <div class="activity-item {expandedFiles.has(filePath) ? 'is-expanded' : ''}" style="padding: 8px 0;">
                  <div class="d-flex justify-content-between align-items-center w-100">
                    <button
                      class="d-flex align-items-center flex-grow-1 text-start border-0 bg-transparent p-0"
                      on:click={() => toggleExpand(filePath)}
                      type="button"
                    >
                      <div class="d-flex align-items-center">
                        <span class="section-chevron me-3 {expandedFiles.has(filePath) ? 'rotated' : ''}">
                          <Fa icon={faChevronRight} aria-hidden="true" />
                        </span>
                        <div class="text-truncate">
                          <span class="activity-details d-block fw-semibold" style="color: var(--text-primary);">
                            {filePath.split(/[\\/]/).pop()}
                          </span>
                          <small class="activity-path text-muted d-block text-truncate">
                            {filePath}
                          </small>
                        </div>
                      </div>
                    </button>

                    <div class="d-flex align-items-center ms-2">
                      <button
                        class="btn btn-sm btn-outline-primary me-3"
                        on:click={(e) => { e.stopPropagation(); selectedFile = filePath; }}
                        title="View History / Restore"
                      >
                        History
                      </button>
                        <span class="badge-soft badge-soft-secondary badge-stack">{fileEvents.length}</span>
                    </div>
                  </div>

                  {#if expandedFiles.has(filePath)}
                    <div class="event-list mt-2" transition:slide={{ duration: 300 }}>
                      {#each fileEvents as event}
                        <div class="event-row">
                          <span class="badge-soft badge-soft-secondary me-3" style="min-width: 80px; justify-content: center;">{event.event_type}</span>
                          <span class="activity-time me-auto">{formatTime(event.timestamp)}</span>
                          {#if event.dest_path}
                            <small class="activity-path text-muted ms-2 text-truncate">&rarr; {event.dest_path.split(/[\\/]/).pop()}</small>
                          {/if}
                        </div>
                      {/each}
                    </div>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/each}
      
      {#if events.length === 0}
        <div class="text-center text-muted py-4">
          <em>No recent activity</em>
        </div>
      {/if}
    </div>
  </div>
</div>

<FileHistoryModal filePath={selectedFile} onClose={() => selectedFile = null} />

<style>
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

  .chip-list span {
    font-size: 0.7rem;
  }

  .badge-stack {
    margin-right: 10px;
    min-width: 40px;
    display: inline-flex;
    justify-content: center;
  }
</style>
