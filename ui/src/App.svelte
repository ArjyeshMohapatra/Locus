<script>
  import { onMount } from 'svelte';
  import { checkHealth } from './api.js';
  import WatchedFolders from './lib/WatchedFolders.svelte';
  import ActivityTimeline from './lib/ActivityTimeline.svelte';

  let status = 'initializing...';

  onMount(async () => {
    const health = await checkHealth();
    status = health.background_service || 'offline';
  });
</script>

<main class="app-container">
  <header class="d-flex justify-content-between align-items-center mb-4">
    <h1 class="h2 mb-0">LOCUS - File Activity Monitor</h1>
    <div class="d-flex align-items-center">
      <span class="status-indicator {status === 'active' ? 'status-healthy' : 'status-error'}"></span>
      <span class="badge {status === 'active' ? 'bg-success' : 'bg-danger'}">
        System: {status}
      </span>
    </div>
  </header>

  <div class="row">
    <div class="col-md-6 mb-4">
      <WatchedFolders />
    </div>
    <div class="col-md-6 mb-4">
      <ActivityTimeline />
    </div>
  </div>
</main>
