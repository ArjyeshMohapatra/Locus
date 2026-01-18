<script>
  import { onMount, onDestroy } from 'svelte';
  import { slide } from 'svelte/transition';
  import { getRecentFileEvents, subscribeFileEvents } from '../api.js';
  import Fa from 'svelte-fa';
  import { faChevronDown, faChevronRight } from '@fortawesome/free-solid-svg-icons';
  import FileHistoryModal from './FileHistoryModal.svelte';

  let events = [];
  let eventSource;
  let selectedFile = null;
  let expandedFiles = new Set();
  
  $: if (typeof document !== 'undefined') {
    const lock = Boolean(selectedFile);
    document.body.style.overflow = lock ? 'hidden' : '';
    document.body.style.overflowX = lock ? 'hidden' : '';
    document.body.style.overscrollBehaviorX = lock ? 'none' : '';
    document.documentElement.style.overflow = lock ? 'hidden' : '';
    document.documentElement.style.overflowX = lock ? 'hidden' : '';
    document.documentElement.style.overscrollBehaviorX = lock ? 'none' : '';
  }
  
  // Grouped events: { "path/to/file": [latest_event, older_event] }
  $: groupedEvents = events.reduce((acc, event) => {
    const path = event.src_path;
    if (!acc[path]) {
      acc[path] = [];
    }
    acc[path].push(event);
    return acc;
  }, {});
  
  // Get list of files sorted by most recent event time
  $: sortedFiles = Object.keys(groupedEvents).sort((a, b) => {
      const timeA = new Date(groupedEvents[a][0].timestamp);
      const timeB = new Date(groupedEvents[b][0].timestamp);
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
      events = [event, ...events].slice(0, 50);
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
    <span class="badge-soft badge-soft-secondary">{sortedFiles.length} Tracks</span>
  </div>
  <div class="card-body p-0">
    <div class="activity-list overflow-auto" style="height: 350px;">
      {#each sortedFiles as filePath}
        {@const fileEvents = groupedEvents[filePath]}
        {@const latestEvent = fileEvents[0]}
        
        <div class="activity-item {expandedFiles.has(filePath) ? 'is-expanded' : ''}">
           <!-- File Header -->
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
                        <small class="text-muted d-block text-truncate" style="font-size: 0.75rem; max-width: 350px;">
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

                     <span class="badge-soft badge-soft-secondary">{fileEvents.length}</span>
               </div>
           </div>
           
           <!-- Expandable Event List -->
           {#if expandedFiles.has(filePath)}
             <div class="event-list mt-2" transition:slide={{ duration: 300 }}>
                 {#each fileEvents as event}
                    <div class="event-row">
                        <span class="badge-soft badge-soft-secondary me-3" style="min-width: 80px; justify-content: center;">{event.event_type}</span>
                        <span class="activity-time me-auto">{formatTime(event.timestamp)}</span>
                        {#if event.dest_path}
                             <small class="text-muted ms-2 text-truncate" style="max-width: 300px;">&rarr; {event.dest_path.split(/[\\/]/).pop()}</small>
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
  /* Expand/collapse is handled via the file header chevron and History button */
</style>
