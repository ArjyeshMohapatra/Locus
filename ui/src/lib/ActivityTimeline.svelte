<script>
  import { onMount, onDestroy } from 'svelte';
  import { slide } from 'svelte/transition';
  import { getRecentFileEvents } from '../api.js';
  import FileHistoryModal from './FileHistoryModal.svelte';

  let events = [];
  let interval;
  let selectedFile = null;
  let expandedFiles = new Set();
  
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
    interval = setInterval(refresh, 2000); // Poll every 2s
  });

  onDestroy(() => {
    clearInterval(interval);
  });
</script>

<div class="card h-100">
  <div class="card-header">
    <h5 class="card-title mb-0">Live File Activity</h5>
  </div>
  <div class="card-body p-0">
    <div class="activity-list" style="height: 250px; overflow-y: auto;">
      {#each sortedFiles as filePath}
        {@const fileEvents = groupedEvents[filePath]}
        {@const latestEvent = fileEvents[0]}
        
        <div class="file-group border-bottom">
           <!-- File Header -->
           <div class="d-flex justify-content-between align-items-center p-2 bg-light">
               <button 
                  class="btn btn-sm btn-link text-decoration-none text-start text-truncate flex-grow-1 p-0 text-dark"
                  on:click={() => toggleExpand(filePath)}
                  type="button"
               >
                 <div class="d-flex align-items-center">
                    <i class="bi {expandedFiles.has(filePath) ? 'bi-chevron-down' : 'bi-chevron-right'} me-2 text-muted" style="font-size: 0.8rem;"></i>
                    <div class="text-truncate">
                        <span class="fw-bold small font-monospace d-block text-truncate">
                            {filePath.split(/[\\/]/).pop()}
                        </span>
                        <small class="text-muted d-block text-truncate" style="font-size: 0.75rem; max-width: 200px;">
                            {filePath}
                        </small>
                    </div>
                 </div>
               </button>
               
               <div class="d-flex align-items-center ms-2">
                   <button 
                       class="btn btn-sm btn-outline-primary me-2 py-0 px-2 rounded-pill" 
                       style="font-size: 0.75rem;"
                       on:click={(e) => { e.stopPropagation(); selectedFile = filePath; }}
                       title="View History / Restore"
                    >
                       History
                   </button>

                     <button
                       class="btn btn-sm btn-outline-secondary me-2 rounded-circle toggle-circle"
                       class:rotated={expandedFiles.has(filePath)}
                       type="button"
                       aria-label={expandedFiles.has(filePath) ? 'Collapse' : 'Expand'}
                       title={expandedFiles.has(filePath) ? 'Collapse' : 'Expand'}
                       on:click={(e) => { e.stopPropagation(); toggleExpand(filePath); }}
                     >
                       <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 15 12 9 18 15"/></svg>
                     </button>
                   <span class="badge bg-secondary rounded-pill" style="font-size: 0.7rem;">{fileEvents.length}</span>
               </div>
           </div>
           
           <!-- Expandable Event List -->
           {#if expandedFiles.has(filePath)}
             <div class="bg-white ps-3 border-top" transition:slide={{ duration: 300 }}>
                 {#each fileEvents as event}
                    <div class="d-flex border-bottom py-2 pe-2 align-items-center" style="font-size: 0.85rem;">
                        <span class="badge bg-light text-dark border me-2 font-monospace" style="width: 80px;">{event.event_type}</span>
                        <span class="text-muted me-auto">{formatTime(event.timestamp)}</span>
                        {#if event.dest_path}
                             <small class="text-muted ms-2 text-truncate" style="max-width: 150px;">&rarr; {event.dest_path.split(/[\\/]/).pop()}</small>
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
  .toggle-circle {
    width: 20px;
    height: 20px;
    padding: 0;
    line-height: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: transform 200ms ease;
  }

  /* Rotate the inner icon for a crisper rotation */
  .toggle-circle svg {
    transition: transform 200ms ease;
    display: inline-block;
    font-size: 0.8rem;
    transform: rotate(0deg);
  }

  .toggle-circle.rotated svg {
    transform: rotate(180deg);
  }
</style>
