<script>
  import { onMount } from 'svelte';
  import Fa from 'svelte-fa';
  import { faMinus, faSquare, faXmark, faCloud } from '@fortawesome/free-solid-svg-icons';

  let appWindow;
  let isMaximized = false;

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
