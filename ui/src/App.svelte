<script>
  import { onMount } from 'svelte';
  import { fade } from 'svelte/transition';
  import { checkHealth } from './api.js';
  import WatchedFolders from './lib/WatchedFolders.svelte';
  import ActivityTimeline from './lib/ActivityTimeline.svelte';
  import SettingsPage from './lib/SettingsPage.svelte';
  import Fa from 'svelte-fa';
  import {
    faBars,
    faFolderOpen,
    faHome,
    faClock,
    faGear
  } from '@fortawesome/free-solid-svg-icons';

  let status = 'initializing...';
  let sidebarOpen = false;
  let currentView = 'dashboard';

  onMount(async () => {
    const health = await checkHealth();
    status = health.background_service || 'offline';
  });

  const toggleSidebar = () => {
    sidebarOpen = !sidebarOpen;
  };

  const setView = (view) => {
    currentView = view;
  };
</script>

<div class="app-shell">
  <aside class="sidebar {sidebarOpen ? 'is-open' : 'is-collapsed'}">
    <button class="hamburger" on:click={toggleSidebar} aria-label="Toggle menu">
      <Fa icon={faBars} class="hamburger-icon" />
    </button>

    <nav class="sidebar-menu">
      <button
        class="sidebar-item {currentView === 'dashboard' ? 'is-active' : ''}"
        on:click={() => setView('dashboard')}
      >
        <span class="sidebar-icon"><Fa icon={faHome} /></span>
        <span class="sidebar-label">Dashboard</span>
      </button>
      <button class="sidebar-item" on:click={() => setView('dashboard')}>
        <span class="sidebar-icon"><Fa icon={faFolderOpen} /></span>
        <span class="sidebar-label">Watched Folders</span>
      </button>
      <button class="sidebar-item" on:click={() => setView('dashboard')}>
        <span class="sidebar-icon"><Fa icon={faClock} /></span>
        <span class="sidebar-label">Activity Timeline</span>
      </button>
      <button
        class="sidebar-item {currentView === 'settings' ? 'is-active' : ''}"
        on:click={() => setView('settings')}
      >
        <span class="sidebar-icon"><Fa icon={faGear} /></span>
        <span class="sidebar-label">Settings</span>
      </button>
    </nav>
  </aside>

  <main class="app-container">
    {#key currentView}
      <div class="view-wrapper" transition:fade={{ duration: 180 }}>
        {#if currentView === 'settings'}
          <SettingsPage />
        {:else}
          <header class="d-flex justify-content-between align-items-center mb-4">
            <h1 class="h2 mb-0">LOCUS - File Activity Monitor</h1>
            <div class="d-flex align-items-center">
              <span
                class="status-indicator {status === 'active' ? 'status-healthy' : 'status-error'}"
              ></span>
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
        {/if}
      </div>
    {/key}
  </main>
</div>
