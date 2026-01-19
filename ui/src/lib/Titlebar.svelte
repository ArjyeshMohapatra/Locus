<script>
  import { onMount, onDestroy } from 'svelte';
  import { subscribeFileEvents } from '../api.js';
  import Fa from 'svelte-fa';
  import { faMinus, faSquare, faXmark, faCloud } from '@fortawesome/free-solid-svg-icons';

  let appWindow;
  let isMaximized = false;
  let eventSource;
  let snapshotProgress = null;

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

  onMount(async () => {
    if (window.__TAURI__) {
      const { appWindow: tauriWindow } = await import('@tauri-apps/api/window');
      appWindow = tauriWindow;
      
      // Update maximize state
      const updateMaximized = async () => {
        isMaximized = await appWindow.isMaximized();
      };
      
      updateMaximized();
      window.addEventListener('resize', updateMaximized);
      return () => window.removeEventListener('resize', updateMaximized);
    }
  });

  onMount(() => {
    eventSource = subscribeFileEvents((event) => {
      if (event?.type === 'snapshot_progress') {
        snapshotProgress = {
          watched_path: event.watched_path,
          total: Number(event.total) || 0,
          processed: Number(event.processed) || 0,
          skipped: Number(event.skipped) || 0,
          error_count: Number(event.error_count) || 0,
          eta_seconds: event.eta_seconds
        };
        return;
      }

      if (event?.type === 'snapshot_complete') {
        snapshotProgress = null;
      }
    });
  });

  onDestroy(() => {
    if (eventSource) {
      eventSource.close();
    }
  });

  const minimize = () => appWindow?.minimize();
  const toggleMaximize = async () => {
    await appWindow?.toggleMaximize();
    isMaximized = await appWindow?.isMaximized();
  };
  const close = () => appWindow?.close();
</script>

<div class="titlebar" data-tauri-drag-region>
  <div class="titlebar-brand" data-tauri-drag-region>
    <span class="titlebar-icon">
      <Fa icon={faCloud} size="xs" />
    </span>
    <span class="titlebar-text">LOCUS</span>
  </div>

  <div class="titlebar-center" data-tauri-drag-region>
    {#if snapshotProgress}
      {@const total = Math.max(snapshotProgress.total, 1)}
      {@const completed = snapshotProgress.processed + snapshotProgress.skipped}
      {@const percent = Math.min(100, Math.round((completed / total) * 100))}
      <div class="snapshot-progress" title={snapshotProgress.watched_path}>
        <div class="snapshot-track" aria-label="Snapshot progress">
          <div class="snapshot-fill" style={`width: ${percent}%`}></div>
        </div>
        <div class="snapshot-meta">
          <span class="snapshot-label">Snapshot</span>
          <span class="snapshot-stats">{percent}% • {formatEta(snapshotProgress.eta_seconds)}</span>
        </div>
      </div>
    {/if}
  </div>

  <div class="titlebar-controls">
    <button class="control-btn" on:click={minimize} title="Minimize">
      <Fa icon={faMinus} size="xs" />
    </button>
    <button class="control-btn" on:click={toggleMaximize} title={isMaximized ? 'Restore' : 'Maximize'}>
      <Fa icon={faSquare} size="xs" />
    </button>
    <button class="control-btn control-close" on:click={close} title="Close">
      <Fa icon={faXmark} size="xs" />
    </button>
  </div>
</div>

<style>
  .titlebar {
    height: 32px;
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

  .snapshot-meta {
    display: flex;
    justify-content: space-between;
    font-size: 0.65rem;
    color: var(--text-muted);
    letter-spacing: 0.02em;
  }

  .snapshot-label {
    font-weight: 600;
    text-transform: uppercase;
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
    width: 46px;
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
