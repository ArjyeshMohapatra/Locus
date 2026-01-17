<script>
  import { onMount } from 'svelte';
  import { showMessage } from '../dialogStore.js';
  import Fa from 'svelte-fa';
  import {
    faFilter,
    faTrashCan,
    faCircleHalfStroke,
    faMoon,
    faSun,
    faPowerOff,
    faGears,
    faChevronDown
  } from '@fortawesome/free-solid-svg-icons';

  let filters = ['node_modules', '.git', '*.tmp'];
  let newFilter = '';

  let gcEnabled = true;
  let gcGraceMinutes = 60;

  let themeMode = 'system';
  let resolvedTheme = 'light';
  let startupMode = 'startup';
  let mediaQuery;

  const addFilter = () => {
    const trimmed = newFilter.trim();
    if (!trimmed) return;
    if (!filters.includes(trimmed)) {
      filters = [...filters, trimmed];
    }
    newFilter = '';
  };

  const removeFilter = (index) => {
    filters = filters.filter((_, i) => i !== index);
  };

  const toggleGc = () => {
    gcEnabled = !gcEnabled;
  };

  const getSystemTheme = () =>
    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';

  const resolveTheme = (mode) => (mode === 'system' ? getSystemTheme() : mode);

  const applyTheme = (mode) => {
    themeMode = mode;
    resolvedTheme = resolveTheme(mode);
    localStorage.setItem('locus-theme', themeMode);
    window.dispatchEvent(new CustomEvent('locus-theme-change', { detail: { mode } }));
  };

  const openStartupSettings = async () => {
    await showMessage('Open Startup Settings (placeholder).', 'Settings');
  };

  onMount(() => {
    const saved = localStorage.getItem('locus-theme');
    themeMode = saved || 'system';

    mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleSystemChange = () => {
      if (themeMode === 'system') {
        resolvedTheme = resolveTheme('system');
      }
    };

    if (mediaQuery.addEventListener) {
      mediaQuery.addEventListener('change', handleSystemChange);
    } else {
      mediaQuery.addListener(handleSystemChange);
    }

    resolvedTheme = resolveTheme(themeMode);

    return () => {
      if (mediaQuery.removeEventListener) {
        mediaQuery.removeEventListener('change', handleSystemChange);
      } else {
        mediaQuery.removeListener(handleSystemChange);
      }
    };
  });

  $: themeIndex = themeMode === 'light' ? 0 : themeMode === 'system' ? 1 : 2;
  $: themeIcon =
    themeMode === 'system' ? faCircleHalfStroke : resolvedTheme === 'dark' ? faMoon : faSun;
</script>

<section class="settings-page">
  <div class="settings-header">
    <h1>Settings</h1>
    <p class="muted">Customize how LOCUS monitors, stores, and displays your activity.</p>
  </div>

  <details class="settings-section" open>
    <summary>
      <div class="section-title">
        <Fa icon={faFilter} class="section-icon" />
        <div>
          <h2>Tracking Filters</h2>
          <p class="muted">Exclude files or folders you never want LOCUS to track.</p>
        </div>
      </div>
      <Fa icon={faChevronDown} class="section-chevron" />
    </summary>
    <div class="settings-content">
      <div class="filter-input">
        <input
          type="text"
          placeholder="Add a folder name or file pattern (e.g., node_modules)"
          bind:value={newFilter}
          on:keydown={(e) => e.key === 'Enter' && addFilter()}
        />
        <button class="btn btn-primary" on:click={addFilter}>Add</button>
      </div>
      <div class="chip-list">
        {#each filters as filter, index}
          <span class="chip">
            {filter}
            <button class="chip-remove" on:click={() => removeFilter(index)}>×</button>
          </span>
        {/each}
      </div>
    </div>
  </details>

  <details class="settings-section" open>
    <summary>
      <div class="section-title">
        <Fa icon={faTrashCan} class="section-icon" />
        <div>
          <h2>Garbage Collector</h2>
          <p class="muted">Control cleanup of older file backups.</p>
        </div>
      </div>
      <Fa icon={faChevronDown} class="section-chevron" />
    </summary>
    <div class="settings-content">
      <div class="settings-row">
        <div>
          <h3>Enable Garbage Collector</h3>
          <p class="muted">Turn this off to keep all backups indefinitely.</p>
        </div>
        <label class="switch">
          <input type="checkbox" bind:checked={gcEnabled} on:change={toggleGc} />
          <span class="slider"></span>
        </label>
      </div>

      <div class="settings-row {gcEnabled ? '' : 'is-disabled'}">
        <div>
          <h3>Grace Period (minutes)</h3>
          <p class="muted">
            Files modified within this window are protected from cleanup.
          </p>
        </div>
        <input
          class="settings-input"
          type="number"
          min="5"
          max="1440"
          step="5"
          bind:value={gcGraceMinutes}
          disabled={!gcEnabled}
        />
      </div>

      <div class="settings-row {gcEnabled ? '' : 'is-disabled'}">
        <div>
          <h3>Cleanup Mode</h3>
          <p class="muted">Automatic cleanup runs in the background when enabled.</p>
        </div>
        <select class="settings-input" disabled={!gcEnabled}>
          <option>Automatic (recommended)</option>
          <option>Manual</option>
        </select>
      </div>
    </div>
  </details>

  <details class="settings-section" open>
    <summary>
      <div class="section-title">
        <Fa icon={themeIcon} class="section-icon" />
        <div>
          <h2>Appearance</h2>
          <p class="muted">Light, system, or GitHub-inspired dark mode.</p>
        </div>
      </div>
      <div class="appearance-actions">
        <div class="segmented-control" style={`--segment-index: ${themeIndex}`}>
          <span class="segment-indicator"></span>
          <button
            class="segment {themeMode === 'light' ? 'is-active' : ''}"
            on:click={() => applyTheme('light')}
          >
            Light
          </button>
          <button
            class="segment {themeMode === 'system' ? 'is-active' : ''}"
            on:click={() => applyTheme('system')}
          >
            System
          </button>
          <button
            class="segment {themeMode === 'dark' ? 'is-active' : ''}"
            on:click={() => applyTheme('dark')}
          >
            Dark
          </button>
        </div>
      </div>
    </summary>
  </details>

  <details class="settings-section" open>
    <summary>
      <div class="section-title">
        <Fa icon={faPowerOff} class="section-icon" />
        <div>
          <h2>Startup & Service</h2>
          <p class="muted">Choose how LOCUS starts when you log in.</p>
        </div>
      </div>
      <Fa icon={faChevronDown} class="section-chevron" />
    </summary>
    <div class="settings-content">
      <div class="radio-group">
        <label class="radio-option">
          <input type="radio" name="startup" value="startup" bind:group={startupMode} />
          <span>
            <strong>Startup App</strong>
            <span class="muted">Launch LOCUS on login and show the main window.</span>
          </span>
        </label>
        <label class="radio-option">
          <input type="radio" name="startup" value="service" bind:group={startupMode} />
          <span>
            <strong>Background Service</strong>
            <span class="muted">Run LOCUS silently as a service after login.</span>
          </span>
        </label>
      </div>

      {#if startupMode === 'startup'}
        <button class="btn btn-outline-secondary" on:click={openStartupSettings}>
          Open Startup Settings
        </button>
      {:else}
        <div class="settings-note">
          Service installation will be available in a future update.
        </div>
      {/if}
    </div>
  </details>

  <details class="settings-section">
    <summary>
      <div class="section-title">
        <Fa icon={faGears} class="section-icon" />
        <div>
          <h2>More Settings</h2>
          <p class="muted">Additional controls will appear here.</p>
        </div>
      </div>
      <Fa icon={faChevronDown} class="section-chevron" />
    </summary>
    <div class="settings-content">
      <div class="settings-note">More options coming soon.</div>
    </div>
  </details>
</section>
