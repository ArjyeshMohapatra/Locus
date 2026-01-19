<script>
  import { onMount, onDestroy } from 'svelte';
  import { fade } from 'svelte/transition';
  import { checkHealth } from './api.js';
  import WatchedFolders from './lib/WatchedFolders.svelte';
  import ActivityTimeline from './lib/ActivityTimeline.svelte';
  import SettingsPage from './lib/SettingsPage.svelte';
  import Titlebar from './lib/Titlebar.svelte';
  import CustomDialog from './lib/CustomDialog.svelte';
  import { errorMessages, clearErrorMessages, removeErrorMessage } from './errorStore.js';
  import Fa from 'svelte-fa';
  import {
    faBars,
    faFolderOpen,
    faHome,
    faClock,
    faGear,
    faMessage
  } from '@fortawesome/free-solid-svg-icons';

  let status = 'initializing...';
  let sidebarOpen = false;
  let currentView = 'dashboard';
  let themeMode = 'system';
  let mediaQuery;
  let notificationsOpen = false;

  const getSystemTheme = () =>
    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';

  const applyTheme = (mode) => {
    const nextTheme = mode === 'system' ? getSystemTheme() : mode;
    document.body.classList.toggle('theme-dark', nextTheme === 'dark');
  };

  let handleSystemChange;
  let handleThemeEvent;

  onMount(async () => {
    const health = await checkHealth();
    status = health.background_service || 'offline';

    themeMode = localStorage.getItem('locus-theme') || 'system';
    applyTheme(themeMode);

    mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    handleSystemChange = () => {
      if (themeMode === 'system') {
        applyTheme('system');
      }
    };

    if (mediaQuery.addEventListener) {
      mediaQuery.addEventListener('change', handleSystemChange);
    } else {
      mediaQuery.addListener(handleSystemChange);
    }

    handleThemeEvent = (event) => {
      themeMode = event.detail?.mode || 'system';
      localStorage.setItem('locus-theme', themeMode);
      applyTheme(themeMode);
    };

    window.addEventListener('locus-theme-change', handleThemeEvent);
  });

  let handleClickOutside;
  let handleNotificationOutside;

  onMount(() => {
    handleClickOutside = (event) => {
      if (sidebarOpen) {
        const sidebar = document.querySelector('.sidebar');
        if (sidebar && !sidebar.contains(event.target)) {
          sidebarOpen = false;
        }
      }
    };
    window.addEventListener('click', handleClickOutside);
  });

  onMount(() => {
    handleNotificationOutside = (event) => {
      if (!notificationsOpen) return;
      const wrapper = document.querySelector('.notification-fab');
      if (wrapper && !wrapper.contains(event.target)) {
        notificationsOpen = false;
      }
    };
    window.addEventListener('click', handleNotificationOutside);
  });

  onDestroy(() => {
    if (handleClickOutside) {
      window.removeEventListener('click', handleClickOutside);
    }
    if (handleNotificationOutside) {
      window.removeEventListener('click', handleNotificationOutside);
    }
    if (mediaQuery && handleSystemChange) {
      if (mediaQuery.removeEventListener) {
        mediaQuery.removeEventListener('change', handleSystemChange);
      } else {
        mediaQuery.removeListener(handleSystemChange);
      }
    }
    if (handleThemeEvent) {
      window.removeEventListener('locus-theme-change', handleThemeEvent);
    }
  });

  const toggleSidebar = () => {
    sidebarOpen = !sidebarOpen;
  };

  const setView = (view) => {
    currentView = view;
  };

  const toggleNotifications = () => {
    notificationsOpen = !notificationsOpen;
  };

  const formatTimestamp = (value) => {
    if (!value) return '';
    const date = new Date(value);
    return new Intl.DateTimeFormat(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      month: 'short',
      day: '2-digit'
    }).format(date);
  };
</script>

<Titlebar />
<CustomDialog />

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
          <header class="d-flex justify-content-between align-items-center mb-5">
            <div>
              <h1 class="fw-bold mb-1">Dashboard</h1>
              <p class="text-muted mb-0">Monitor your file ecosystem in real-time.</p>
            </div>
            <div class="d-flex align-items-center gap-3">
              <div class="d-flex align-items-center gap-2 px-3 py-2 rounded-pill status-pill">
                <span class="status-indicator {status === 'active' ? 'status-healthy' : 'status-error'}"></span>
                <span class="fw-medium small text-uppercase {status === 'active' ? 'text-success' : 'text-danger'}">
                   {status}
                </span>
              </div>
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

{#if currentView === 'dashboard'}
  <div class="notification-fab">
    <button class="fab-button" on:click={toggleNotifications} aria-label="Open notifications">
      <Fa icon={faMessage} />
      {#if $errorMessages.length > 0}
        <span class="fab-badge">{$errorMessages.length}</span>
      {/if}
    </button>

    {#if notificationsOpen}
      <div class="fab-popover">
        <div class="fab-header">
          <span class="fw-semibold">Messages</span>
          <button class="btn btn-sm btn-link" on:click={clearErrorMessages}>
            Clear
          </button>
        </div>

        {#if $errorMessages.length === 0}
          <div class="fab-empty">No errors yet.</div>
        {:else}
          <div class="fab-list">
            {#each $errorMessages as item (item.id)}
              <div class="fab-item">
                <div class="fab-item-text">
                  <div class="fab-item-message">{item.message}</div>
                  <div class="fab-item-time">{formatTimestamp(item.timestamp)}</div>
                </div>
                <button class="fab-remove" on:click={() => removeErrorMessage(item.id)}>
                  ×
                </button>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  </div>
{/if}

<style>
  .notification-fab {
    position: fixed;
    right: 24px;
    bottom: 24px;
    z-index: 2000;
  }

  .fab-button {
    position: relative;
    width: 52px;
    height: 52px;
    border-radius: 50%;
    border: none;
    background: var(--primary-500, #4f46e5);
    box-shadow: 0 12px 30px rgba(0, 0, 0, 0.2);
    cursor: pointer;
    display: grid;
    place-items: center;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
  }

  .fab-button:hover {
    transform: translateY(-2px);
    box-shadow: 0 16px 32px rgba(0, 0, 0, 0.25);
  }

  .fab-button:active {
    transform: translateY(0);
    box-shadow: 0 10px 24px rgba(0, 0, 0, 0.2);
  }

  .fab-button :global(svg) {
    color: #fff;
    width: 1.1rem;
    height: 1.1rem;
  }

  .fab-badge {
    position: absolute;
    top: -6px;
    right: -6px;
    background: #ef4444;
    color: #fff;
    font-size: 0.7rem;
    padding: 2px 6px;
    border-radius: 999px;
  }

  .fab-popover {
    position: absolute;
    right: 0;
    bottom: 64px;
    width: 320px;
    max-height: 360px;
    background: #fff;
    border-radius: 14px;
    box-shadow: 0 16px 30px rgba(0, 0, 0, 0.2);
    border: 1px solid rgba(0, 0, 0, 0.08);
    display: flex;
    flex-direction: column;
  }

  :global(.theme-dark) .fab-popover {
    background: #0f172a;
    color: #e2e8f0;
    border-color: rgba(148, 163, 184, 0.2);
    box-shadow: 0 18px 32px rgba(0, 0, 0, 0.45);
  }

  .fab-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  }

  :global(.theme-dark) .fab-header {
    border-bottom-color: rgba(148, 163, 184, 0.2);
  }

  .fab-list {
    padding: 12px 16px;
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .fab-item {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 10px 12px;
    background: rgba(248, 250, 252, 0.9);
    border-radius: 10px;
    border: 1px solid rgba(0, 0, 0, 0.05);
  }

  :global(.theme-dark) .fab-item {
    background: rgba(30, 41, 59, 0.8);
    border-color: rgba(148, 163, 184, 0.2);
  }

  .fab-item-text {
    font-size: 0.82rem;
    color: #1f2937;
    flex: 1;
  }

  :global(.theme-dark) .fab-item-text {
    color: #e2e8f0;
  }

  .fab-item-message {
    margin-bottom: 4px;
  }

  .fab-item-time {
    font-size: 0.7rem;
    color: #6b7280;
  }

  :global(.theme-dark) .fab-item-time {
    color: #94a3b8;
  }

  .fab-remove {
    border: none;
    background: transparent;
    font-size: 1rem;
    cursor: pointer;
    color: #6b7280;
  }

  :global(.theme-dark) .fab-remove {
    color: #94a3b8;
  }

  .fab-empty {
    padding: 18px;
    color: #6b7280;
    font-size: 0.85rem;
  }

  :global(.theme-dark) .fab-empty {
    color: #94a3b8;
  }

  .btn-link {
    color: var(--primary-500, #4f46e5);
    text-decoration: none;
  }
</style>
