<script>
  import { onMount, onDestroy } from 'svelte';
  import {
    checkHealth,
    getAuthStatus,
    getDashboardSummary,
    lockAuth,
    getRuntimeSettings,
    sendTelemetryEvent
  } from './api.js';
  import { listen } from '@tauri-apps/api/event';
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
  let uiZoomScale = 1;
  let dashboardSummary = { total_files: 0, total_versions: 0, storage_bytes: 0, ram_usage_bytes: 0, db_size_bytes: 0, total_snapshots: 0, last_snapshot_time: null };

  let healthRefreshTimer;
  let themeRefreshTimer;
  let themeTransitionTimer;
  let tauriThemeUnlisten;
  let locusThemeUnlisten;
  let linuxThemeUnlisten;
  let systemThemeOverride = null;
  const MIN_UI_ZOOM_SCALE = 0.5;
  const MAX_UI_ZOOM_SCALE = 3;
  const DEFAULT_UI_ZOOM_SCALE = 1;
  const THEME_TRANSITION_MS = 220;
  const TELEMETRY_WINDOW_MS = 10000;
  const TELEMETRY_WINDOW_MAX_EVENTS = 8;

  let telemetryEventWindow = [];

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const clampUiZoomScale = (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return DEFAULT_UI_ZOOM_SCALE;
    return Math.min(MAX_UI_ZOOM_SCALE, Math.max(MIN_UI_ZOOM_SCALE, parsed));
  };

  const canSendTelemetryEvent = () => {
    const now = Date.now();
    telemetryEventWindow = telemetryEventWindow.filter((timestamp) => now - timestamp <= TELEMETRY_WINDOW_MS);
    if (telemetryEventWindow.length >= TELEMETRY_WINDOW_MAX_EVENTS) {
      return false;
    }
    telemetryEventWindow.push(now);
    return true;
  };

  const reportUiTelemetry = ({ eventType, message, stack = null, context = {}, severity = 'error' }) => {
    const normalizedMessage = String(message || '').trim();
    if (!normalizedMessage || !canSendTelemetryEvent()) {
      return;
    }

    void sendTelemetryEvent({
      source: 'ui',
      event_type: eventType,
      severity,
      message: normalizedMessage,
      stack: stack ? String(stack) : null,
      context: {
        current_view: currentView,
        ...context
      },
      timestamp: new Date().toISOString()
    }).catch(() => {
      // Do not surface telemetry failures to users.
    });
  };

  const applyUiZoomScale = (value, { persist = false } = {}) => {
    const clamped = clampUiZoomScale(value);
    uiZoomScale = clamped;
    document.documentElement.style.zoom = String(clamped);

    if (persist) {
      try {
        localStorage.setItem('locus-ui-zoom', String(clamped));
      } catch {
        // Ignore storage errors in restricted runtime contexts.
      }
    }
  };

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

  const normalizeThemeValue = (value) => {
    if (value === 'dark' || value === 'light') return value;
    if (typeof value !== 'string') return null;
    const normalized = value.toLowerCase();
    if (normalized.includes('dark')) return 'dark';
    if (normalized.includes('light')) return 'light';
    return null;
  };

  const getSystemTheme = () => {
    if (systemThemeOverride) return systemThemeOverride;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  };

  const applyTheme = (mode, { animate = true } = {}) => {
    const nextTheme = mode === 'system' ? getSystemTheme() : mode;
    const shouldUseDark = nextTheme === 'dark';
    const isDarkApplied = document.body.classList.contains('theme-dark');

    if (isDarkApplied === shouldUseDark) return;

    if (animate) {
      document.body.classList.add('theme-transitioning');
      if (themeTransitionTimer) {
        clearTimeout(themeTransitionTimer);
      }
      themeTransitionTimer = setTimeout(() => {
        document.body.classList.remove('theme-transitioning');
      }, THEME_TRANSITION_MS);
    }

    document.body.classList.toggle('theme-dark', shouldUseDark);
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
      applyUiZoomScale(runtime?.ui_zoom_scale ?? uiZoomScale, { persist: true });
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
    const savedZoomScale = localStorage.getItem('locus-ui-zoom');
    applyUiZoomScale(savedZoomScale ?? DEFAULT_UI_ZOOM_SCALE);

    await refreshAuthState();
    await refreshRuntimeSettings();
    authChecked = true;

    await refreshHealthStatus({ retries: 10, retryDelayMs: 500 });

    if (!isLocked && !isSetupRequired) {
      await refreshDashboardSummaries();
    }

    themeMode = localStorage.getItem('locus-theme') || 'system';
    applyTheme(themeMode, { animate: false });

    mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    handleSystemChange = () => {
      if (themeMode === 'system') {
        systemThemeOverride = null;
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
      if (themeMode !== 'system') {
        systemThemeOverride = null;
      }
      applyTheme(themeMode);
    };

    handleRuntimeSettingsEvent = (event) => {
      if (typeof event.detail?.runInBackgroundService === 'boolean') {
        runInBackgroundService = event.detail.runInBackgroundService;
      }
      if (event.detail?.uiZoomScale !== undefined) {
        applyUiZoomScale(event.detail.uiZoomScale, { persist: true });
      }
    };

    window.addEventListener('locus-theme-change', handleThemeEvent);
    window.addEventListener('locus-runtime-settings-change', handleRuntimeSettingsEvent);

    // Desktop app specifics
    try {
      const applyThemePayload = (payload) => {
        const normalizedTheme = normalizeThemeValue(payload);
        if (!normalizedTheme) return;
        systemThemeOverride = normalizedTheme;
        if (themeMode === 'system') {
          applyTheme('system');
        }
      };

      tauriThemeUnlisten = await listen('tauri://theme-changed', (event) => {
        applyThemePayload(event.payload);
      });

      locusThemeUnlisten = await listen('locus://theme-changed', (event) => {
        applyThemePayload(event.payload);
      });

      linuxThemeUnlisten = await listen('locus://linux-system-theme-changed', (event) => {
        applyThemePayload(event.payload);
      });

      themeRefreshTimer = setInterval(() => {
        if (themeMode === 'system') {
          applyTheme('system');
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
  let handleContextMenuBlock;
  let handleRefreshShortcutBlock;
  let handleWindowError;
  let handleUnhandledRejection;
  const isDeveloperRuntime = Boolean(import.meta.env?.DEV);

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

  onMount(() => {
    handleContextMenuBlock = (event) => {
      event.preventDefault();
    };

    handleRefreshShortcutBlock = (event) => {
      const key = String(event.key || '').toLowerCase();
      const isCtrlOrCmdR = (event.ctrlKey || event.metaKey) && key === 'r';
      const isF5 = key === 'f5';

      if (!isCtrlOrCmdR && !isF5) return;

      // Dev convenience: allow Ctrl/Cmd+R only in development runtime.
      if (isDeveloperRuntime && isCtrlOrCmdR) return;

      event.preventDefault();
      event.stopPropagation();
    };

    handleWindowError = (event) => {
      const message = event?.error?.message || event?.message || 'Unhandled UI error';
      const stack = event?.error?.stack || null;
      reportUiTelemetry({
        eventType: 'unhandled_error',
        message,
        stack,
        severity: 'error',
        context: {
          filename: event?.filename || '',
          line: Number(event?.lineno || 0),
          column: Number(event?.colno || 0)
        }
      });
    };

    handleUnhandledRejection = (event) => {
      const reason = event?.reason;
      const message = reason?.message || String(reason || 'Unhandled promise rejection');
      const stack = reason?.stack || null;
      reportUiTelemetry({
        eventType: 'unhandled_rejection',
        message,
        stack,
        severity: 'error'
      });
    };

    window.addEventListener('contextmenu', handleContextMenuBlock, true);
    window.addEventListener('keydown', handleRefreshShortcutBlock, true);
    window.addEventListener('error', handleWindowError);
    window.addEventListener('unhandledrejection', handleUnhandledRejection);
  });

  onDestroy(() => {
    if (handleClickOutside) {
      window.removeEventListener('click', handleClickOutside);
    }
    if (handleNotificationOutside) {
      window.removeEventListener('click', handleNotificationOutside);
    }
    if (handleContextMenuBlock) {
      window.removeEventListener('contextmenu', handleContextMenuBlock, true);
    }
    if (handleRefreshShortcutBlock) {
      window.removeEventListener('keydown', handleRefreshShortcutBlock, true);
    }
    if (handleWindowError) {
      window.removeEventListener('error', handleWindowError);
    }
    if (handleUnhandledRejection) {
      window.removeEventListener('unhandledrejection', handleUnhandledRejection);
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
    if (typeof tauriThemeUnlisten === 'function') {
      tauriThemeUnlisten();
    }
    if (typeof locusThemeUnlisten === 'function') {
      locusThemeUnlisten();
    }
    if (typeof linuxThemeUnlisten === 'function') {
      linuxThemeUnlisten();
    }
    if (themeRefreshTimer) {
      clearInterval(themeRefreshTimer);
    }
    if (themeTransitionTimer) {
      clearTimeout(themeTransitionTimer);
      themeTransitionTimer = null;
    }
    document.body.classList.remove('theme-transitioning');
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
  <div class="d-flex align-items-center justify-content-center" style="height: 100vh;">Loading Locus...</div>
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
    <button
      class="hamburger sidebar-tooltip-target"
      on:click={toggleSidebar}
      aria-label="Toggle menu"
      data-tooltip={sidebarOpen ? null : 'Menu'}
    >
      <span class="sidebar-icon hamburger-icon"><Fa icon={faBars} /></span>
      <span class="sidebar-label sidebar-hamburger-label">Menu</span>
    </button>

    <nav class="sidebar-menu">
      <button
        class="sidebar-item sidebar-tooltip-target {currentView === 'dashboard' ? 'is-active' : ''}"
        on:click={() => setView('dashboard')}
        aria-label="Dashboard"
        data-tooltip={sidebarOpen ? null : 'Dashboard'}
      >
        <span class="sidebar-icon"><Fa icon={faHome} /></span>
        <span class="sidebar-label">Dashboard</span>
      </button>
      <button
        class="sidebar-item sidebar-tooltip-target {currentView === 'watched' ? 'is-active' : ''}"
        on:click={() => setView('watched')}
        aria-label="Watched folders"
        data-tooltip={sidebarOpen ? null : 'Watched Folders'}
      >
        <span class="sidebar-icon"><Fa icon={faFolderOpen} /></span>
        <span class="sidebar-label">Watched Folders</span>
      </button>
      <button
        class="sidebar-item sidebar-tooltip-target {currentView === 'activity' ? 'is-active' : ''}"
        on:click={() => setView('activity')}
        aria-label="Activity timeline"
        data-tooltip={sidebarOpen ? null : 'Activity Timeline'}
      >
        <span class="sidebar-icon"><Fa icon={faClock} /></span>
        <span class="sidebar-label">Activity Timeline</span>
      </button>
      <button
        class="sidebar-item sidebar-tooltip-target {currentView === 'checkpoints' ? 'is-active' : ''}"
        on:click={() => setView('checkpoints')}
        aria-label="Checkpoints"
        data-tooltip={sidebarOpen ? null : 'Checkpoints'}
      >
        <span class="sidebar-icon"><Fa icon={faDatabase} /></span>
        <span class="sidebar-label">Checkpoints</span>
      </button>
      <button
        class="sidebar-item sidebar-tooltip-target {currentView === 'snapshots' ? 'is-active' : ''}"
        on:click={() => setView('snapshots')}
        aria-label="Snapshot history"
        data-tooltip={sidebarOpen ? null : 'Snapshot History'}
      >
        <span class="sidebar-icon"><Fa icon={faBookOpen} /></span>
        <span class="sidebar-label">Snapshot History</span>
      </button>
      <button
        class="sidebar-item sidebar-tooltip-target {currentView === 'settings' ? 'is-active' : ''}"
        on:click={() => setView('settings')}
        aria-label="Settings"
        data-tooltip={sidebarOpen ? null : 'Settings'}
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
          <header class="view-header">
          <div>
              <h1>Watched Folders</h1>
              <p class="view-subtitle">Manage tracked folders and relink locations.</p>
          </div>
        </header>
        <WatchedFolders />
      {:else if currentView === 'activity'}
          <header class="view-header">
          <div>
              <h1>Live Activity</h1>
              <p class="view-subtitle">View recent file events and restore from history.</p>
          </div>
        </header>
        <ActivityTimeline />
      {:else if currentView === 'checkpoints'}
        <CheckpointSessionsPage />
      {:else if currentView === 'snapshots'}
        <SnapshotHistoryPage />
      {:else}
          <header class="view-header dashboard-header">
          <div>
              <h1>Command Center</h1>
              <p class="view-subtitle">Operational overview across monitoring, storage, and snapshots.</p>
          </div>
            <div class="dashboard-actions">
              <div class="status-pill">
              <span class="status-indicator {status === 'active' ? 'status-healthy' : 'status-error'}"></span>
                <span class="status-label {status === 'active' ? 'status-label-ok' : 'status-label-bad'}">
                  {status}
              </span>
            </div>
              <button class="btn btn-danger btn-sm d-flex align-items-center lock-btn" on:click={executeLockApp} title="Lock Application">
                <Fa icon={faLock} class="me-2" />
                <span>Lock Vault</span>
            </button>
          </div>
        </header>

          <section class="dashboard-section">
            <div class="dashboard-section-head">
              <h2>Core Metrics</h2>
            </div>
            <div class="metric-grid">
              <article class="metric-tile metric-files">
                <div class="metric-kicker">Tracked Files</div>
                <div class="metric-value">{dashboardSummary.total_files.toLocaleString()}</div>
                <div class="metric-icon"><Fa icon={faFolderOpen} /></div>
              </article>

              <article class="metric-tile metric-versions">
                <div class="metric-kicker">Versions Preserved</div>
                <div class="metric-value">{dashboardSummary.total_versions.toLocaleString()}</div>
                <div class="metric-icon"><Fa icon={faClock} /></div>
              </article>

              <article class="metric-tile metric-storage">
                <div class="metric-kicker">Storage Utilized</div>
                <div class="metric-value">
                  {(dashboardSummary.storage_bytes / (1024 * 1024)).toFixed(1)}
                  <span class="metric-value-unit">MB</span>
                </div>
                <div class="metric-icon"><Fa icon={faGear} /></div>
              </article>
            </div>
          </section>

          <section class="dashboard-section">
            <div class="dashboard-section-head">
              <h2 class="d-flex align-items-center gap-2">
                <Fa icon={faServer} /> Runtime Health
              </h2>
              <span class="section-note">Live resource usage from active processes.</span>
            </div>
            <div class="metric-grid">
              <article class="metric-tile metric-ram">
                <div class="metric-kicker">Combined RAM Usage</div>
                <div class="metric-value">
                  {(dashboardSummary.ram_usage_bytes / (1024 * 1024)).toFixed(1)}
                  <span class="metric-value-unit">MB</span>
                </div>
                <div class="metric-icon"><Fa icon={faMemory} /></div>
              </article>

              <article class="metric-tile metric-db">
                <div class="metric-kicker">Database Size</div>
                <div class="metric-value">
                  {(dashboardSummary.db_size_bytes / 1024).toFixed(1)}
                  <span class="metric-value-unit">KB</span>
                </div>
                <div class="metric-icon"><Fa icon={faDatabase} /></div>
              </article>

              <article class="metric-tile metric-snapshots">
                <div class="metric-kicker">Snapshot Activity</div>
                <div class="metric-value">{dashboardSummary.total_snapshots.toLocaleString()}</div>
                <div class="metric-meta">
                  <span class="metric-meta-label">Last captured</span>
                  {#if dashboardSummary.last_snapshot_time}
                    <span>{formatTimestamp(dashboardSummary.last_snapshot_time)}</span>
                  {:else}
                    <span class="text-muted">Awaiting activity...</span>
                  {/if}
                </div>
                <div class="metric-icon"><Fa icon={faHeartPulse} /></div>
              </article>
            </div>
          </section>

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
  .vault-toast {
    position: fixed;
    top: 70px;
    left: 50%;
    transform: translateX(-50%);
    background: var(--accent);
    color: #fff;
    padding: 8px 14px;
    border-radius: 999px;
    z-index: 9999;
    font-weight: 600;
    font-size: 0.84rem;
    animation: fadeOut 3s forwards;
  }

  @keyframes fadeOut {
    0% {
      opacity: 0;
      transform: translate(-50%, -8px);
    }

    10% {
      opacity: 1;
      transform: translate(-50%, 0);
    }

    80% {
      opacity: 1;
    }

    100% {
      opacity: 0;
      display: none;
    }
  }

  .view-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 1rem;
    margin-bottom: 1.35rem;
  }

  .view-header h1 {
    margin: 0;
    font-size: 1.58rem;
    letter-spacing: -0.01em;
    font-weight: 700;
  }

  .view-subtitle {
    margin: 0.3rem 0 0;
    color: var(--text-muted);
    max-width: 58ch;
  }

  .dashboard-header {
    margin-bottom: 1.15rem;
  }

  .dashboard-actions {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.42rem 0.75rem;
    border-radius: 999px;
    background: var(--surface-elevated);
    border: 1px solid var(--border-subtle);
    box-shadow: var(--shadow-sm);
  }

  .status-pill .status-healthy {
    animation: none;
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--success) 24%, transparent);
  }

  .status-pill .status-error {
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--danger) 24%, transparent);
  }

  .status-label {
    font-size: 0.74rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: none;
  }

  .status-label-ok {
    color: var(--success);
  }

  .status-label-bad {
    color: var(--danger);
  }

  .lock-btn {
    border-radius: 999px;
    min-height: 34px;
    padding: 0.34rem 0.78rem;
    border-width: 1px;
    box-shadow: none;
    font-size: 0.74rem;
    font-weight: 700;
    line-height: 1;
  }

  .lock-btn:hover {
    color: #fff;
  }

  .dashboard-section {
    margin-bottom: 1rem;
  }

  .dashboard-section-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.6rem;
    margin-bottom: 0.72rem;
  }

  .dashboard-section-head h2 {
    margin: 0;
    font-size: 0.92rem;
    text-transform: none;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    font-weight: 700;
  }

  .section-note {
    font-size: 0.78rem;
    color: var(--text-muted);
  }

  .metric-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.75rem;
  }

  .metric-tile {
    position: relative;
    border: 1px solid var(--border-subtle);
    border-radius: 12px;
    background: var(--surface-elevated);
    min-height: 150px;
    padding: 0.95rem 1rem;
    box-shadow: var(--shadow-sm);
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    overflow: hidden;
  }

  .metric-kicker {
    font-size: 0.74rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: none;
    color: var(--text-muted);
  }

  .metric-value {
    font-size: clamp(1.45rem, 2.6vw, 2rem);
    line-height: 1.15;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--text-primary);
  }

  .metric-value-unit {
    font-size: 0.95rem;
    color: var(--text-muted);
    font-weight: 600;
    margin-left: 0.2rem;
  }

  .metric-meta {
    margin-top: auto;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    font-size: 0.8rem;
    color: var(--text-primary);
  }

  .metric-meta-label {
    font-size: 0.7rem;
    text-transform: none;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    font-weight: 700;
  }

  .metric-icon {
    position: absolute;
    right: 0.9rem;
    bottom: 0.8rem;
    width: 34px;
    height: 34px;
    border-radius: 10px;
    display: grid;
    place-items: center;
    color: var(--accent);
    background: var(--accent-soft);
  }

  .metric-icon :global(svg) {
    width: 1rem;
    height: 1rem;
  }

  .metric-files .metric-icon {
    color: #0a5dc2;
  }

  .metric-versions .metric-icon {
    color: #0f6b7f;
  }

  .metric-storage .metric-icon {
    color: #1a7f37;
  }

  .metric-ram .metric-icon {
    color: #1f5fbf;
  }

  .metric-db .metric-icon {
    color: #8a5900;
  }

  .metric-snapshots .metric-icon {
    color: #17653f;
  }

  :global(body.theme-dark) .metric-db .metric-icon {
    color: #d5a85d;
  }

  .notification-fab {
    position: fixed;
    right: 20px;
    bottom: 20px;
    z-index: 2000;
  }

  .fab-button {
    position: relative;
    width: 46px;
    height: 46px;
    border-radius: 12px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    box-shadow: var(--shadow-md);
    cursor: pointer;
    display: grid;
    place-items: center;
    transition: border-color 0.15s ease, background-color 0.15s ease;
  }

  .fab-button:hover {
    border-color: var(--border-strong);
    background: var(--surface-soft);
  }

  .fab-button :global(svg) {
    color: var(--accent);
    width: 1rem;
    height: 1rem;
  }

  .fab-badge {
    position: absolute;
    top: -5px;
    right: -5px;
    background: var(--danger);
    color: #fff;
    font-size: 0.68rem;
    min-width: 18px;
    height: 18px;
    padding: 0 5px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 2px solid var(--surface-elevated);
  }

  .fab-popover {
    position: absolute;
    right: 0;
    bottom: 58px;
    width: 340px;
    max-height: 380px;
    background: var(--surface-elevated);
    border-radius: 12px;
    box-shadow: var(--shadow-lg);
    border: 1px solid var(--border-subtle);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .fab-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 14px;
    border-bottom: 1px solid var(--border-subtle);
  }

  .fab-list {
    padding: 10px 12px;
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .fab-item {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 9px 10px;
    background: var(--surface-soft);
    border-radius: 10px;
    border: 1px solid var(--border-subtle);
  }

  .fab-item-text {
    font-size: 0.82rem;
    color: var(--text-primary);
    flex: 1;
  }

  .fab-item-message {
    margin-bottom: 4px;
  }

  .fab-item-time {
    font-size: 0.7rem;
    color: var(--text-muted);
  }

  .fab-remove {
    border: none;
    background: transparent;
    font-size: 1rem;
    cursor: pointer;
    color: var(--text-muted);
    line-height: 1;
  }

  .fab-remove:hover {
    color: var(--text-primary);
  }

  .fab-empty {
    padding: 16px;
    color: var(--text-muted);
    font-size: 0.85rem;
  }

  .btn-link {
    color: var(--accent);
    text-decoration: none;
  }

  @media (max-width: 1100px) {
    .metric-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (max-width: 760px) {
    .view-header {
      flex-direction: column;
      align-items: flex-start;
      gap: 0.75rem;
    }

    .dashboard-actions {
      width: 100%;
      justify-content: flex-start;
    }

    .dashboard-section-head {
      flex-direction: column;
      align-items: flex-start;
      gap: 0.35rem;
    }

    .metric-grid {
      grid-template-columns: minmax(0, 1fr);
    }

    .fab-popover {
      width: min(92vw, 340px);
      right: -8px;
    }
  }
</style>
