<script>
  import { onMount, onDestroy } from 'svelte';
  import {
    checkHealth,
    getAuthStatus,
    getDashboardSummary,
    lockAuth,
    getRuntimeSettings
  } from './api.js';
  import { listen } from '@tauri-apps/api/event';
  import { appWindow } from '@tauri-apps/api/window';
  import WatchedFolders from './lib/WatchedFolders.svelte';
  import ActivityTimeline from './lib/ActivityTimeline.svelte';
  import SettingsPage from './lib/SettingsPage.svelte';
  import SnapshotHistoryPage from './lib/SnapshotHistoryPage.svelte';
  import CheckpointSessionsPage from './lib/CheckpointSessionsPage.svelte';
  import Titlebar from './lib/Titlebar.svelte';
  import CustomDialog from './lib/CustomDialog.svelte';
  import LockScreen from './lib/LockScreen.svelte';
  import { errorMessages, clearErrorMessages, removeErrorMessage } from './errorStore.js';
  import Fa from 'svelte-fa';
  import {
    faBars,
    faFolderOpen,
    faHome,
    faClock,
    faGear,
    faMessage,
    faBookOpen,
    faLock,
    faServer,
    faMemory,
    faDatabase,
    faHeartPulse
  } from '@fortawesome/free-solid-svg-icons';

  let status = 'initializing...';
  let authChecked = false;
  let isLocked = false;
  let isSetupRequired = false;
  let showUnlockToast = false;
  let sidebarOpen = false;
  let currentView = 'dashboard';
  let themeMode = 'system';
  let mediaQuery;
  let notificationsOpen = false;
  let runInBackgroundService = true;
  let dashboardSummary = { total_files: 0, total_versions: 0, storage_bytes: 0, ram_usage_bytes: 0, db_size_bytes: 0, total_snapshots: 0, last_snapshot_time: null };

  let healthRefreshTimer;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const refreshHealthStatus = async ({ retries = 1, retryDelayMs = 0 } = {}) => {
    let latest = { background_service: 'offline' };
    for (let attempt = 0; attempt < retries; attempt += 1) {
      latest = await checkHealth();
      const background = latest?.background_service || 'offline';
      status = background;
      if (background !== 'offline') {
        return latest;
      }
      if (attempt < retries - 1 && retryDelayMs > 0) {
        await sleep(retryDelayMs);
      }
    }
    return latest;
  };

  const getSystemTheme = () =>
    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';

  const applyTheme = (mode) => {
    const nextTheme = mode === 'system' ? getSystemTheme() : mode;
    document.body.classList.toggle('theme-dark', nextTheme === 'dark');
  };

  let handleSystemChange;
  let handleThemeEvent;
  let handleRuntimeSettingsEvent;

  const refreshAuthState = async () => {
    try {
      const authRes = await getAuthStatus();
      isLocked = !!authRes.locked;
      isSetupRequired = !!authRes.setup_required;
    } catch (e) {
      console.error('Auth status fetch failed:', e);
      // Conservative fallback: if setup cannot be verified, prefer unlock screen over setup screen.
      isSetupRequired = false;
      isLocked = true;
    }
  };

  const refreshRuntimeSettings = async () => {
    try {
      const runtime = await getRuntimeSettings();
      runInBackgroundService = runtime?.run_in_background_service ?? true;
    } catch (e) {
      console.error('Runtime settings fetch failed:', e);
      runInBackgroundService = true;
    }
  };

  const handleUnlocked = async () => {
    isLocked = false;
    isSetupRequired = false;
    showUnlockToast = true;
    setTimeout(() => { showUnlockToast = false; }, 3000);
    if (currentView === 'dashboard') {
      await refreshDashboardSummaries();
    }
  };

  const handleSetupExists = async () => {
    await refreshAuthState();
  };

  onMount(async () => {
    await refreshAuthState();
    await refreshRuntimeSettings();
    authChecked = true;

    await refreshHealthStatus({ retries: 10, retryDelayMs: 500 });

    if (!isLocked && !isSetupRequired) {
      await refreshDashboardSummaries();
    }

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

    handleRuntimeSettingsEvent = (event) => {
      runInBackgroundService = !!event.detail?.runInBackgroundService;
    };

    window.addEventListener('locus-theme-change', handleThemeEvent);
    window.addEventListener('locus-runtime-settings-change', handleRuntimeSettingsEvent);

    // Desktop app specifics
    try {
      await listen('tauri://theme-changed', (event) => {
        if (themeMode === 'system') {
          // payload is 'light' or 'dark'
          document.body.classList.toggle('theme-dark', event.payload === 'dark');
        }
      });

      setInterval(async () => {
        if (themeMode === 'system') {
          let isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
          try {
            const wTheme = await appWindow.theme();
            if (wTheme === 'dark') isDark = true;
            if (wTheme === 'light') isDark = false;
          } catch {}
          document.body.classList.toggle('theme-dark', isDark);
        }
      }, 2000);
    } catch {
      // Ignored for normal browser envs
    }

    // Keep status in sync if backend restarts after app launch.
    healthRefreshTimer = setInterval(() => {
      refreshHealthStatus({ retries: 1 });
      if (currentView === 'dashboard' && !isLocked && authChecked) {
        refreshDashboardSummaries();
      }
    }, 10000);
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
    if (handleRuntimeSettingsEvent) {
      window.removeEventListener('locus-runtime-settings-change', handleRuntimeSettingsEvent);
    }
    if (healthRefreshTimer) {
      clearInterval(healthRefreshTimer);
    }
  });

  const toggleSidebar = () => {
    sidebarOpen = !sidebarOpen;
  };

  const setView = (view) => {
    currentView = view;
    if (view === 'dashboard') {
      refreshDashboardSummaries();
    }
  };

  const toggleNotifications = () => {
    notificationsOpen = !notificationsOpen;
  };

  const formatTimestamp = (value) => {
    if (!value) return '';
    // Ensure naive UTC strings are treated as UTC by the Date constructor
    let normalized = value;
    if (typeof value === 'string' && !value.endsWith('Z') && !value.includes('+') && !value.includes('-')) {
      normalized = value.replace(' ', 'T') + 'Z';
    }
    const date = new Date(normalized);
    return new Intl.DateTimeFormat(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      month: 'short',
      day: '2-digit'
    }).format(date);
  };

  const refreshDashboardSummaries = async () => {
    try {
      const summaryData = await getDashboardSummary();
      dashboardSummary = summaryData || { total_files: 0, total_versions: 0, storage_bytes: 0, ram_usage_bytes: 0, db_size_bytes: 0, total_snapshots: 0, last_snapshot_time: null };
    } catch (e) {
      console.error('Failed to refresh dashboard summaries', e);
      dashboardSummary = { total_files: 0, total_versions: 0, storage_bytes: 0, ram_usage_bytes: 0, db_size_bytes: 0, total_snapshots: 0, last_snapshot_time: null };
    }
  };

  const executeLockApp = async () => {
    try {
      await lockAuth();
      isLocked = true;
    } catch (e) {
      console.error("Lock app failed:", e);
    }
  };
</script>

<CustomDialog />

{#if !authChecked}
  <div class="d-flex align-items-center justify-content-center" style="height: 100vh;">Loading LOCUS...</div>
{:else if isLocked || isSetupRequired}
  <Titlebar closeBehavior="shutdown" />
  <LockScreen {isSetupRequired} on:unlocked={handleUnlocked} on:setup-exists={handleSetupExists} />
{:else}
  {#if showUnlockToast}
    <div class="vault-toast">Vault Unlocked</div>
  {/if}
  <Titlebar closeBehavior={runInBackgroundService ? 'tray' : 'shutdown'} />

  <div class="app-shell">
  <aside class="sidebar {sidebarOpen ? 'is-open' : 'is-collapsed'}">
    <button class="hamburger" on:click={toggleSidebar} aria-label="Toggle menu">
      <span class="sidebar-icon hamburger-icon"><Fa icon={faBars} /></span>
      <span class="sidebar-label sidebar-hamburger-label">Menu</span>
    </button>

    <nav class="sidebar-menu">
      <button
        class="sidebar-item {currentView === 'dashboard' ? 'is-active' : ''}"
        on:click={() => setView('dashboard')}
      >
        <span class="sidebar-icon"><Fa icon={faHome} /></span>
        <span class="sidebar-label">Dashboard</span>
      </button>
      <button
        class="sidebar-item {currentView === 'watched' ? 'is-active' : ''}"
        on:click={() => setView('watched')}
      >
        <span class="sidebar-icon"><Fa icon={faFolderOpen} /></span>
        <span class="sidebar-label">Watched Folders</span>
      </button>
      <button
        class="sidebar-item {currentView === 'activity' ? 'is-active' : ''}"
        on:click={() => setView('activity')}
      >
        <span class="sidebar-icon"><Fa icon={faClock} /></span>
        <span class="sidebar-label">Activity Timeline</span>
      </button>
      <button
        class="sidebar-item {currentView === 'checkpoints' ? 'is-active' : ''}"
        on:click={() => setView('checkpoints')}
      >
        <span class="sidebar-icon"><Fa icon={faDatabase} /></span>
        <span class="sidebar-label">Checkpoints</span>
      </button>
      <button
        class="sidebar-item {currentView === 'snapshots' ? 'is-active' : ''}"
        on:click={() => setView('snapshots')}
      >
        <span class="sidebar-icon"><Fa icon={faBookOpen} /></span>
        <span class="sidebar-label">Snapshot History</span>
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
    <div class="view-wrapper {(currentView === 'dashboard' || currentView === 'snapshots') ? 'view-wrapper-no-scrollbar' : ''}">
      {#if currentView === 'settings'}
        <SettingsPage />
      {:else if currentView === 'watched'}
        <header class="d-flex justify-content-between align-items-center mb-5">
          <div>
            <h1 class="fw-bold mb-1">Watched Folders</h1>
            <p class="text-muted mb-0">Manage tracked folders and relink locations.</p>
          </div>
        </header>
        <WatchedFolders />
      {:else if currentView === 'activity'}
        <header class="d-flex justify-content-between align-items-center mb-5">
          <div>
            <h1 class="fw-bold mb-1">Live Activity</h1>
            <p class="text-muted mb-0">View recent file events and restore from history.</p>
          </div>
        </header>
        <ActivityTimeline />
      {:else if currentView === 'checkpoints'}
        <CheckpointSessionsPage />
      {:else if currentView === 'snapshots'}
        <SnapshotHistoryPage />
      {:else}
        <header class="d-flex justify-content-between align-items-center mb-5">
          <div>
            <h1 class="fw-bold mb-1">Command Center</h1>
            <p class="text-muted mb-0">Real-time overview of your LOCUS ecosystem.</p>
          </div>
          <div class="d-flex align-items-center gap-3">
            <div class="d-flex align-items-center gap-2 px-3 py-2 rounded-pill status-pill">
              <span class="status-indicator {status === 'active' ? 'status-healthy' : 'status-error'}"></span>
              <span class="fw-medium small text-uppercase {status === 'active' ? 'text-success' : 'text-danger'}">
                 {status}
              </span>
            </div>
            <!-- Lock App action -->
             <button class="btn btn-outline-danger d-flex align-items-center rounded-pill px-3 py-2 shadow-sm lock-btn" on:click={executeLockApp} title="Lock Application">
                <Fa icon={faLock} class="me-2" />
                <span class="fw-bold small text-uppercase ls-1">Lock Vault</span>
            </button>
          </div>
        </header>

        <!-- Metrics Highlight Bar -->
        <div class="row mb-5 fade-in">
          <div class="col-md-4 mb-3 mb-md-0">
             <div class="card bg-gradient-primary text-white border-0 rounded-4 shadow-sm h-100 overflow-hidden position-relative p-4 metric-card">
                 <div class="position-relative z-1">
                     <div class="text-white-50 small fw-bold text-uppercase tracking-wider mb-2">Tracked Files</div>
                     <h2 class="display-5 fw-bold mb-0">{dashboardSummary.total_files.toLocaleString()}</h2>
                 </div>
                 <div class="position-absolute opacity-10" style="bottom: -15px; right: -5px; font-size: 5rem;">
                    <Fa icon={faFolderOpen} />
                 </div>
             </div>
          </div>
          <div class="col-md-4 mb-3 mb-md-0">
             <div class="card bg-gradient-info text-white border-0 rounded-4 shadow-sm h-100 overflow-hidden position-relative p-4 metric-card">
                 <div class="position-relative z-1">
                     <div class="text-white-50 small fw-bold text-uppercase tracking-wider mb-2">Versions Preserved</div>
                     <h2 class="display-5 fw-bold mb-0">{dashboardSummary.total_versions.toLocaleString()}</h2>
                 </div>
                 <div class="position-absolute opacity-10" style="bottom: -15px; right: -5px; font-size: 5rem;">
                    <Fa icon={faClock} />
                 </div>
             </div>
          </div>
          <div class="col-md-4">
             <div class="card bg-gradient-success text-white border-0 rounded-4 shadow-sm h-100 overflow-hidden position-relative p-4 metric-card">
                 <div class="position-relative z-1">
                     <div class="text-white-50 small fw-bold text-uppercase tracking-wider mb-2">Storage Utilized</div>
                     <h2 class="display-5 fw-bold mb-0">{(dashboardSummary.storage_bytes / (1024 * 1024)).toFixed(1)} <span class="fs-4">MB</span></h2>
                 </div>
                 <div class="position-absolute opacity-10" style="bottom: -15px; right: -5px; font-size: 5rem;">
                    <Fa icon={faGear} />
                 </div>
             </div>
          </div>
        </div>

        <!-- Resource Monitor Highlight Bar -->
        <h5 class="fw-bold mb-3 d-flex align-items-center gap-2">
            <Fa icon={faServer} class="text-secondary" /> System Resource Monitor
        </h5>
        <div class="row mb-5 fade-in">
          <div class="col-md-4 mb-3 mb-md-0">
             <div class="card bg-glass text-body border-0 rounded-4 shadow-sm h-100 overflow-hidden position-relative p-4 metric-card">
                 <div class="position-relative z-1">
                     <div class="text-muted small fw-bold text-uppercase tracking-wider mb-2">Active RAM Usage</div>
                     <h2 class="display-6 fw-bold mb-0">{(dashboardSummary.ram_usage_bytes / (1024 * 1024)).toFixed(1)} <span class="fs-5 text-muted">MB</span></h2>
                 </div>
                 <div class="position-absolute opacity-10 text-primary" style="bottom: -15px; right: -5px; font-size: 5rem;">
                    <Fa icon={faMemory} />
                 </div>
             </div>
          </div>
          <div class="col-md-4 mb-3 mb-md-0">
             <div class="card bg-glass text-body border-0 rounded-4 shadow-sm h-100 overflow-hidden position-relative p-4 metric-card">
                 <div class="position-relative z-1">
                     <div class="text-muted small fw-bold text-uppercase tracking-wider mb-2">Database Size</div>
                     <h2 class="display-6 fw-bold mb-0">{(dashboardSummary.db_size_bytes / 1024).toFixed(1)} <span class="fs-5 text-muted">KB</span></h2>
                 </div>
                 <div class="position-absolute opacity-10 text-info" style="bottom: -15px; right: -5px; font-size: 5rem;">
                    <Fa icon={faDatabase} />
                 </div>
             </div>
          </div>
          <div class="col-md-4">
             <div class="card bg-glass text-body border-0 rounded-4 shadow-sm h-100 overflow-hidden position-relative p-4 metric-card">
                 <div class="position-relative z-1">
                     <div class="text-muted small fw-bold text-uppercase tracking-wider mb-2">Snapshot Activity</div>
                     <h2 class="display-6 fw-bold mb-1">{dashboardSummary.total_snapshots.toLocaleString()}</h2>
                     <div class="small fw-medium">
                         {#if dashboardSummary.last_snapshot_time}
                            <span class="text-success">Last captured:</span> {formatTimestamp(dashboardSummary.last_snapshot_time)}
                         {:else}
                            <span class="text-muted fst-italic">Awaiting activity...</span>
                         {/if}
                     </div>
                 </div>
                 <div class="position-absolute opacity-10 text-success" style="bottom: -15px; right: -5px; font-size: 5rem;">
                    <Fa icon={faHeartPulse} />
                 </div>
             </div>
          </div>
        </div>

      {/if}
    </div>
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

{/if}

<style>
  .vault-toast { position: fixed; top: 70px; left: 50%; transform: translateX(-50%); background: var(--accent); color: white; padding: 8px 16px; border-radius: 20px; z-index: 9999; font-weight: 500; font-size: 0.9rem; animation: fadeOut 3s forwards; }
  @keyframes fadeOut { 0% { opacity: 0; transform: translate(-50%, -10px); } 10% { opacity: 1; transform: translate(-50%, 0); } 80% { opacity: 1; } 100% { opacity: 0; display: none; } }
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
    background: var(--accent);
    box-shadow: 0 12px 30px rgba(0, 0, 0, 0.2);
    cursor: pointer;
    display: grid;
    place-items: center;
    transition: box-shadow 0.15s ease, background-color 0.15s ease;
  }

  .fab-button:active {
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
    background: var(--surface-elevated, #fff);
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
    border-bottom: 1px solid var(--border-subtle);
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
    background: var(--surface-soft, rgba(248, 250, 252, 0.9));
    border-radius: 10px;
    border: 1px solid var(--border-subtle);
  }

  :global(.theme-dark) .fab-item {
    background: rgba(30, 41, 59, 0.8);
    border-color: rgba(148, 163, 184, 0.2);
  }

  .fab-item-text {
    font-size: 0.82rem;
    color: var(--text-primary);
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
    color: var(--text-muted);
  }

  :global(.theme-dark) .fab-item-time {
    color: #94a3b8;
  }

  .fab-remove {
    border: none;
    background: transparent;
    font-size: 1rem;
    cursor: pointer;
    color: var(--text-muted);
  }

  :global(.theme-dark) .fab-remove {
    color: #94a3b8;
  }

  .fab-empty {
    padding: 18px;
    color: var(--text-muted);
    font-size: 0.85rem;
  }

  :global(.theme-dark) .fab-empty {
    color: #94a3b8;
  }

  .btn-link {
    color: var(--accent);
    text-decoration: none;
  }

  /* Dashboard Enhance Styles */
  .bg-gradient-primary { background: linear-gradient(135deg, #4f46e5 0%, #312e81 100%); }
  .bg-gradient-info { background: linear-gradient(135deg, #0ea5e9 0%, #0369a1 100%); }
  .bg-gradient-success { background: linear-gradient(135deg, #10b981 0%, #047857 100%); }
  .tracking-wider { letter-spacing: 0.05em; }
  .ls-1 { letter-spacing: 0.04em; }
  .opacity-10 { opacity: 0.1; }
  .lock-btn { transition: all 0.2s ease; border-width: 2px; }
  .lock-btn:hover { background-color: var(--bs-danger); color: white; border-color: var(--bs-danger); }
  .metric-card { transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1); }
  .bg-glass { background: var(--surface-soft, rgba(255, 255, 255, 0.8)); }
  :global(.theme-dark) .bg-glass { background: rgba(30, 41, 59, 0.5); }
</style>
