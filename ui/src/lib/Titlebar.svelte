<svelte:options runes={false} />

<script>
  import { onMount, onDestroy } from 'svelte';
  import { getCurrentWindow } from '@tauri-apps/api/window';
  import { exit as tauriExit } from '@tauri-apps/plugin-process';
  import { stopWatchedSnapshotScan, subscribeFileEvents } from '../api.js';
  import { askQuestion, showMessage } from '../dialogStore.js';
  import Fa from 'svelte-fa';
  import { faMinus, faSquare, faXmark, faCloud } from '@fortawesome/free-solid-svg-icons';

  export let closeBehavior = 'tray';

  let appWindow;
  let isMaximized = false;
  let eventSource;
  let snapshotProgress = null;
  let stoppingSnapshot = false;
  let stopRequestToast = '';
  let stopRequestToastTimer;
  let detachWindowResizeListener = null;
  let decorationRetryTimer = null;
  let customTitlebarActive = false;

  const isTauriRuntime = () => (
    typeof window !== 'undefined' && !!(window.__TAURI__ || window.__TAURI_INTERNALS__ || window.__TAURI_IPC__)
  );

  const setCustomTitlebarState = (enabled) => {
    customTitlebarActive = !!enabled;
    document.body.classList.toggle('has-custom-titlebar', customTitlebarActive);
  };

  const ensureUndecoratedWindow = async () => {
    if (!appWindow) return false;

    if (typeof appWindow.setDecorations === 'function') {
      try {
        await appWindow.setDecorations(false);
      } catch (error) {
        console.error('Failed to disable native window decorations:', error);
      }
    }

    if (typeof appWindow.isDecorated === 'function') {
      try {
        return !(await appWindow.isDecorated());
      } catch (error) {
        console.error('Failed to confirm window decoration state:', error);
      }
    }

    return true;
  };

  const formatEta = (seconds) => {
    if (seconds === null || seconds === undefined) return 'ETA --:--';
    const totalSeconds = Math.max(Number(seconds) || 0, 0);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const secs = Math.floor(totalSeconds % 60);
    if (hours > 0) {
      return `ETA ${hours}h ${String(minutes).padStart(2, '0')}m`;
    }
    return `ETA ${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  };

  onMount(() => {
    setCustomTitlebarState(false);
    const initWindowControls = async () => {
      if (!isTauriRuntime()) {
        setCustomTitlebarState(true);
        return;
      }

      try {
        appWindow = getCurrentWindow();
        setCustomTitlebarState(await ensureUndecoratedWindow());

        const updateMaximized = async () => {
          if (!appWindow || typeof appWindow.isMaximized !== 'function') return;
          isMaximized = await appWindow.isMaximized();
        };

        await updateMaximized();
        window.addEventListener('resize', updateMaximized);
        detachWindowResizeListener = () => window.removeEventListener('resize', updateMaximized);

        // Some Linux window managers re-apply decorations shortly after maximize/show.
        decorationRetryTimer = setTimeout(async () => {
          setCustomTitlebarState(await ensureUndecoratedWindow());
        }, 300);
      } catch (error) {
        console.error('Failed to initialize titlebar controls:', error);
      }
    };

    void initWindowControls();
  });

  onMount(() => {
    eventSource = subscribeFileEvents((event) => {
      if (event?.type === 'snapshot_start') {
        snapshotProgress = {
          watched_path: event.watched_path,
          total: 0,
          processed: 0,
          skipped: 0,
          error_count: 0,
          eta_seconds: null,
          scanning: true
        };
        stoppingSnapshot = false;
        return;
      }

      if (event?.type === 'snapshot_progress') {
        snapshotProgress = {
          watched_path: event.watched_path,
          total: Number(event.total) || 0,
          processed: Number(event.processed) || 0,
          skipped: Number(event.skipped) || 0,
          error_count: Number(event.error_count) || 0,
          eta_seconds: event.eta_seconds,
          scanning: false
        };
        return;
      }

      if (event?.type === 'snapshot_complete') {
        snapshotProgress = null;
        stoppingSnapshot = false;
      }
    });
  });

  onDestroy(() => {
    if (typeof detachWindowResizeListener === 'function') {
      detachWindowResizeListener();
      detachWindowResizeListener = null;
    }
    if (decorationRetryTimer) {
      clearTimeout(decorationRetryTimer);
      decorationRetryTimer = null;
    }
    if (stopRequestToastTimer) {
      clearTimeout(stopRequestToastTimer);
      stopRequestToastTimer = null;
    }
    if (eventSource) {
      eventSource.close();
    }
    setCustomTitlebarState(false);
  });

  const showStopRequestToast = (message) => {
    stopRequestToast = String(message || '').trim();
    if (stopRequestToastTimer) {
      clearTimeout(stopRequestToastTimer);
    }
    stopRequestToastTimer = setTimeout(() => {
      stopRequestToast = '';
    }, 1700);
  };

  const handleTitlebarMouseDown = async (event) => {
    if (!appWindow || !isTauriRuntime() || event.button !== 0) return;

    const target = event.target;
    if (target instanceof Element && target.closest('.titlebar-controls')) {
      return;
    }

    if (typeof appWindow.startDragging !== 'function') return;

    try {
      await appWindow.startDragging();
    } catch {
      // Keep native drag-region behavior as the primary path.
    }
  };

  const handleTitlebarDoubleClick = async (event) => {
    const target = event.target;
    if (target instanceof Element && target.closest('.titlebar-controls')) {
      return;
    }
    await toggleMaximize();
  };

  const minimize = async () => {
    try {
      await appWindow?.minimize();
    } catch (e) {
      console.error('Failed to minimize window:', e);
    }
  };
  const toggleMaximize = async () => {
    try {
      await appWindow?.toggleMaximize();
      isMaximized = await appWindow?.isMaximized();
    } catch (e) {
      console.error('Failed to toggle maximize:', e);
    }
  };
  const close = async () => {
    try {
      if (closeBehavior === 'shutdown') {
        if (isTauriRuntime()) {
          await tauriExit(0);
          return;
        }
        window.close();
        return;
      }

      if (!appWindow) return;
      if (typeof appWindow.hide === 'function') {
        await appWindow.hide();
        return;
      }
      await appWindow.close?.();
    } catch (e) {
      console.error('Failed to process close action:', e);
    }
  };

  const handleStopSnapshotScan = async () => {
    const watchedPath = String(snapshotProgress?.watched_path || '').trim();
    if (!watchedPath || stoppingSnapshot) {
      return;
    }

    const confirmed = await askQuestion(
      'This will stop the active snapshot scan, remove the watched folder from LOCUS, and delete related backup/storage data. This action cannot be undone.',
      'Stop Scan And Delete Data',
      {
        type: 'danger',
        okLabel: 'Stop And Delete',
        cancelLabel: 'Cancel'
      }
    );
    if (!confirmed) {
      return;
    }

    stoppingSnapshot = true;
    showStopRequestToast('Stop requested');
    try {
      const response = await stopWatchedSnapshotScan(watchedPath, {
        removeWatchedPath: true,
        purgeStorage: true
      });
      showStopRequestToast('Stopping and removing folder');

      const result = response?.result || {};
      const storageCleanup = response?.storage_cleanup || {};
      const snapshotDirCleanup = storageCleanup?.snapshot_dir || {};
      const summaryLines = [
        `Watched path: ${watchedPath}`,
        `File versions removed: ${Number(result?.file_versions_deleted || 0)}`,
        `File events removed: ${Number(result?.file_events_deleted || 0)}`,
        `Backup tasks removed: ${Number(result?.backup_tasks_deleted || 0)}`,
        `Snapshot jobs removed: ${Number(result?.snapshot_jobs_deleted || 0)}`,
        `Storage files removed: ${Number(snapshotDirCleanup?.deleted_files || 0)}`
      ];

      await showMessage(
        summaryLines.join('\n'),
        'Scan Stopped: Data Removed',
        'danger'
      );
    } catch (error) {
      console.error('Failed to stop snapshot scan:', error);
      showStopRequestToast('Could not request stop');
      await showMessage(
        error?.message || 'Failed to stop and remove snapshot data.',
        'Stop Failed',
        'error'
      );
    } finally {
      stoppingSnapshot = false;
    }
  };
</script>

{#if customTitlebarActive}
  <div
    class="titlebar"
    role="toolbar"
    tabindex="0"
    aria-label="Window title bar"
    on:mousedown={handleTitlebarMouseDown}
    on:dblclick={handleTitlebarDoubleClick}
  >
    <div class="titlebar-brand" data-tauri-drag-region>
      <span class="titlebar-icon">
        <Fa icon={faCloud} />
      </span>
      <span class="titlebar-text">Locus</span>
    </div>

    <div class="titlebar-center" data-tauri-drag-region>
      {#if snapshotProgress}
        {@const total = Math.max(snapshotProgress.total, 1)}
        {@const completed = snapshotProgress.processed + snapshotProgress.skipped}
        {@const percent = Math.min(100, Math.round((completed / total) * 100))}
        {@const scanning = snapshotProgress.scanning || snapshotProgress.total <= 0}
        <div class="snapshot-progress" title={snapshotProgress.watched_path}>
          <div class="snapshot-track" aria-label="Snapshot progress">
            <div
              class="snapshot-fill {scanning ? 'is-indeterminate' : ''}"
              style={scanning ? '' : `width: ${percent}%`}
            ></div>
          </div>
          <div class="snapshot-meta">
            <span class="snapshot-label">{scanning ? 'Scanning' : 'Snapshot'}</span>
            <span class="snapshot-stats">
              {scanning ? 'Preparing…' : `${percent}% • ${formatEta(snapshotProgress.eta_seconds)}`}
            </span>
            <button
              class="snapshot-stop-btn"
              on:mousedown|stopPropagation
              on:dblclick|stopPropagation
              on:click|stopPropagation={handleStopSnapshotScan}
              disabled={stoppingSnapshot}
              title="Stop current scan"
            >
              {stoppingSnapshot ? 'Stopping…' : 'Stop'}
            </button>
          </div>
          {#if stopRequestToast}
            <div class="snapshot-stop-toast">{stopRequestToast}</div>
          {/if}
        </div>
      {/if}
    </div>

    <div class="titlebar-controls">
      <button class="control-btn" on:click={minimize} title="Minimize">
        <Fa icon={faMinus} />
      </button>
      <button class="control-btn" on:click={toggleMaximize} title={isMaximized ? 'Restore' : 'Maximize'}>
        <Fa icon={faSquare} />
      </button>
      <button class="control-btn control-close" on:click={close} title="Close">
        <Fa icon={faXmark} />
      </button>
    </div>
  </div>
{/if}

<style>
  .titlebar {
    height: 40px;
    background: var(--sidebar-bg);
    display: flex;
    justify-content: space-between;
    align-items: center;
    user-select: none;
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 1000;
    border-bottom: 1px solid var(--sidebar-border);
  }

  .titlebar-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding-left: 12px;
    height: 100%;
    color: var(--text-muted);
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.05em;
  }

  .titlebar-icon {
    color: var(--accent);
    display: flex;
    align-items: center;
  }

  .titlebar-controls {
    display: flex;
    height: 100%;
    margin-left: auto;
    position: relative;
    z-index: 2;
  }

  .titlebar-center {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    height: 100%;
    pointer-events: none;
  }

  .snapshot-progress {
    display: grid;
    gap: 2px;
    min-width: 220px;
    max-width: 380px;
    width: 32vw;
    pointer-events: auto;
    position: relative;
  }

  .snapshot-track {
    height: 6px;
    border-radius: 999px;
    background: rgba(148, 163, 184, 0.3);
    overflow: hidden;
  }

  .snapshot-fill {
    height: 100%;
    background: var(--accent);
    border-radius: inherit;
    transition: width 0.2s ease;
  }

  .snapshot-fill.is-indeterminate {
    position: relative;
    width: 40%;
    animation: indeterminate 1.2s ease-in-out infinite;
  }

  .snapshot-meta {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.65rem;
    color: var(--text-muted);
    letter-spacing: 0.02em;
    gap: 0.35rem;
  }

  .snapshot-label {
    font-weight: 600;
    text-transform: none;
  }

  .snapshot-stop-btn {
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-secondary);
    border-radius: 999px;
    padding: 0.06rem 0.5rem;
    font-size: 0.62rem;
    line-height: 1.15;
    cursor: pointer;
  }

  .snapshot-stop-btn:hover:enabled {
    color: var(--text-primary);
    border-color: var(--border-strong);
  }

  .snapshot-stop-btn:disabled {
    opacity: 0.7;
    cursor: wait;
  }

  .snapshot-stop-toast {
    position: absolute;
    top: calc(100% + 4px);
    right: 0;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-primary);
    border-radius: 999px;
    padding: 0.2rem 0.58rem;
    font-size: 0.62rem;
    font-weight: 600;
    white-space: nowrap;
    box-shadow: var(--shadow-sm);
    pointer-events: none;
  }

  @keyframes indeterminate {
    0% {
      transform: translateX(-120%);
    }
    100% {
      transform: translateX(240%);
    }
  }

  @media (max-width: 760px) {
    .titlebar-center {
      display: none;
    }
  }

  .control-btn {
    display: inline-flex;
    justify-content: center;
    align-items: center;
    width: 48px;
    height: 100%;
    border: none;
    background: transparent;
    color: var(--text-muted);
    transition: all 0.2s;
    cursor: pointer;
  }

  .control-btn:hover {
    background: var(--sidebar-hover);
    color: var(--text-primary);
  }

  .control-close:hover {
    background: #e81123 !important;
    color: white !important;
  }

  :global(body.theme-dark) .titlebar {
    background: var(--sidebar-bg);
  }
</style>
