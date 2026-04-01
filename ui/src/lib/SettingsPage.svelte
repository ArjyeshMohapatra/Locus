<script>
  import { onMount } from 'svelte';
  import { showMessage } from '../dialogStore.js';
  import {
    getSecuritySettings,
    setSecuritySettings,
    getTrackingExclusions,
    setTrackingExclusions,
    getSnapshotSettings,
    updateSnapshotSettings,
    getRuntimeSettings,
    updateRuntimeSettings
  } from '../api.js';
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

  let excludedFolders = [];
  let customExclusions = [];
  let newExclusion = '';
  let exclusionsLoading = false;
  let exclusionsSaving = false;
  let exclusionsError = '';

  let gcEnabled = true;
  let gcGraceMinutes = 60;

  let adminProtectionEnabled = false;
  let adminProtectionLoading = false;
  let adminProtectionError = '';
  let adminProtectionInfo = '';
  let isAdminUser = false;

  let themeMode = 'system';
  let resolvedTheme = 'light';
  let mediaQuery;

  let snapshotSettingsLoading = false;
  let snapshotSettingsSaving = false;
  let snapshotSettingsError = '';
  let snapshotIntervalSeconds = 10;
  let snapshotRetentionDays = 10;
  let snapshotExcludePrivate = true;
  let snapshotCaptureOnWindowChange = true;
  let snapshotAllowDelete = false;
  let snapshotVaultInfo = '';

  let runtimeSettingsLoading = false;
  let runtimeSettingsSaving = false;
  let runtimeSettingsError = '';
  let runInBackgroundService = true;
  let uiZoomScale = 1;
  const MIN_UI_ZOOM_SCALE = 0.5;
  const MAX_UI_ZOOM_SCALE = 3;
  const UI_ZOOM_STEP = 0.05;


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

    loadSecuritySettings();
    loadTrackingExclusions();
    loadSnapshotSettings();
    loadRuntimeSettings();

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


  const loadSecuritySettings = async () => {
    adminProtectionLoading = true;
    adminProtectionError = '';
    try {
      const data = await getSecuritySettings();
      adminProtectionEnabled = !!data.admin_protection_enabled;
      isAdminUser = !!data.is_admin;
    } catch (e) {
      adminProtectionError = e.message || 'Failed to load security settings.';
    } finally {
      adminProtectionLoading = false;
    }
  };

  const loadTrackingExclusions = async () => {
    exclusionsLoading = true;
    exclusionsError = '';
    try {
      const data = await getTrackingExclusions();
      excludedFolders = data.excluded_directories || [];
      customExclusions = data.custom_exclusions || [];
    } catch (e) {
      exclusionsError = e.message || 'Failed to load tracking exclusions.';
    } finally {
      exclusionsLoading = false;
    }
  };

  const persistExclusions = async (next) => {
    exclusionsSaving = true;
    exclusionsError = '';
    try {
      await setTrackingExclusions(next);
      customExclusions = next;
    } catch (e) {
      exclusionsError = e.message || 'Failed to update exclusions.';
    } finally {
      exclusionsSaving = false;
    }
  };

  const addCustomExclusion = async () => {
    const trimmed = newExclusion.trim();
    if (!trimmed) return;
    if (customExclusions.includes(trimmed)) {
      newExclusion = '';
      return;
    }
    const next = [...customExclusions, trimmed];
    newExclusion = '';
    await persistExclusions(next);
  };

  const removeCustomExclusion = async (index) => {
    const next = customExclusions.filter((_, i) => i !== index);
    await persistExclusions(next);
  };

  const toggleAdminProtection = async () => {
    adminProtectionLoading = true;
    adminProtectionError = '';
    adminProtectionInfo = '';
    const nextValue = !adminProtectionEnabled;

    try {
      await setSecuritySettings(nextValue);
      adminProtectionEnabled = nextValue;
      adminProtectionInfo = nextValue
        ? 'Admin protection enabled. Backup files are now restricted.'
        : 'Admin protection disabled.';
    } catch (e) {
      adminProtectionError = e.message || 'Failed to update admin protection.';
    } finally {
      adminProtectionLoading = false;
    }
  };

  const loadSnapshotSettings = async () => {
    snapshotSettingsLoading = true;
    snapshotSettingsError = '';
    snapshotVaultInfo = '';
    try {
      const data = await getSnapshotSettings();
      snapshotIntervalSeconds = data.interval_seconds ?? 10;
      snapshotRetentionDays = data.retention_days ?? 10;
      snapshotExcludePrivate = !!data.exclude_private_browsing;
      snapshotCaptureOnWindowChange = data.capture_on_window_change ?? true;
      snapshotAllowDelete = !!data.allow_individual_delete;
    } catch (e) {
      snapshotSettingsError = e.message || 'Failed to load snapshot settings.';
    } finally {
      snapshotSettingsLoading = false;
    }
  };

  const saveSnapshotSettings = async () => {
    snapshotSettingsSaving = true;
    snapshotSettingsError = '';
    try {
      await updateSnapshotSettings({
        interval_seconds: Number(snapshotIntervalSeconds),
        retention_days: Number(snapshotRetentionDays),
        exclude_private_browsing: !!snapshotExcludePrivate,
        capture_on_window_change: !!snapshotCaptureOnWindowChange,
        allow_individual_delete: !!snapshotAllowDelete
      });
    } catch (e) {
      snapshotSettingsError = e.message || 'Failed to apply snapshot settings.';
    } finally {
      snapshotSettingsSaving = false;
    }
  };

  const loadRuntimeSettings = async () => {
    runtimeSettingsLoading = true;
    runtimeSettingsError = '';
    try {
      const data = await getRuntimeSettings();
      runInBackgroundService = data.run_in_background_service ?? true;
      const parsedZoom = Number(data.ui_zoom_scale ?? 1);
      uiZoomScale = Number.isFinite(parsedZoom)
        ? Math.min(MAX_UI_ZOOM_SCALE, Math.max(MIN_UI_ZOOM_SCALE, parsedZoom))
        : 1;
    } catch (e) {
      runtimeSettingsError = e.message || 'Failed to load runtime settings.';
    } finally {
      runtimeSettingsLoading = false;
    }
  };

  const emitRuntimeSettingsChange = () => {
    window.dispatchEvent(
      new CustomEvent('locus-runtime-settings-change', {
        detail: {
          runInBackgroundService,
          uiZoomScale
        }
      })
    );
  };

  const previewUiZoomScale = () => {
    emitRuntimeSettingsChange();
  };

  const clampUiZoomScale = (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return 1;
    return Math.min(MAX_UI_ZOOM_SCALE, Math.max(MIN_UI_ZOOM_SCALE, parsed));
  };

  const normalizeUiZoomScale = (value) =>
    Number(clampUiZoomScale(value).toFixed(2));

  const nudgeUiZoomScale = async (delta) => {
    uiZoomScale = normalizeUiZoomScale(Number(uiZoomScale) + delta);
    previewUiZoomScale();
    await saveRuntimeSettings({ silent: true });
  };

  const commitUiZoomScaleInput = async () => {
    uiZoomScale = normalizeUiZoomScale(uiZoomScale);
    previewUiZoomScale();
    await saveRuntimeSettings({ silent: true });
  };

  const saveRuntimeSettings = async ({ silent = false } = {}) => {
    runtimeSettingsSaving = true;
    runtimeSettingsError = '';
    try {
      const data = await updateRuntimeSettings({
        run_in_background_service: !!runInBackgroundService,
        ui_zoom_scale: Number(uiZoomScale)
      });
      runInBackgroundService = data.run_in_background_service ?? runInBackgroundService;
      const parsedZoom = Number(data.ui_zoom_scale ?? uiZoomScale);
      uiZoomScale = Number.isFinite(parsedZoom)
        ? Math.min(MAX_UI_ZOOM_SCALE, Math.max(MIN_UI_ZOOM_SCALE, parsedZoom))
        : uiZoomScale;
      emitRuntimeSettingsChange();
      if (!silent) {
        await showMessage('Runtime preferences applied.', 'Settings');
      }
    } catch (e) {
      runtimeSettingsError = e.message || 'Failed to apply runtime settings.';
    } finally {
      runtimeSettingsSaving = false;
    }
  };


</script>

<section class="settings-page">
  <div class="settings-header">
    <h1>Settings</h1>
    <p class="muted">Customize how Locus monitors, stores, and displays your activity.</p>
  </div>

  <details class="settings-section" open>
    <summary>
      <div class="section-title">
        <Fa icon={faFilter} class="section-icon" />
        <div>
          <h2>Tracking Filters</h2>
          <p class="muted">Exclude folders or file patterns from tracking.</p>
        </div>
      </div>
      <Fa icon={faChevronDown} class="section-chevron" />
    </summary>
    <div class="settings-content">
      {#if exclusionsLoading}
        <div class="settings-note">Loading exclusions…</div>
      {:else if exclusionsError}
        <div class="settings-note text-danger">{exclusionsError}</div>
      {:else}
        <details class="settings-note">
          <summary>Default exclusion:
            <Fa icon={faChevronDown} class="section-chevron" />
          </summary>
          <div class="chip-list" style="margin-top:8px;">
            {#each excludedFolders as folder}
              <span class="chip is-readonly">{folder}</span>
            {/each}
          </div>
        </details>

        <div class="settings-note" style="margin-top: 12px;">Custom exclusions:</div>
        <div class="filter-input">
          <input
            type="text"
            placeholder="Add a folder name or file pattern (e.g., node_modules)"
            bind:value={newExclusion}
            on:keydown={(e) => e.key === 'Enter' && addCustomExclusion()}
            disabled={exclusionsSaving}
          />
          <button class="btn btn-primary" on:click={addCustomExclusion} disabled={exclusionsSaving}>
            {exclusionsSaving ? 'Saving…' : 'Add'}
          </button>
        </div>
        <div class="chip-list">
          {#each customExclusions as folder, index}
            <span class="chip">
              {folder}
              <button class="chip-remove" on:click={() => removeCustomExclusion(index)} disabled={exclusionsSaving}>
                ×
              </button>
            </span>
          {/each}
        </div>
      {/if}
    </div>
  </details>

  <details class="settings-section" open>
    <summary>
      <div class="section-title">
        <Fa icon={faGears} class="section-icon" />
        <div>
          <h2>Snapshot Memory</h2>
          <p class="muted">Tune interval, retention, privacy, and deletion behavior.</p>
        </div>
      </div>
      <Fa icon={faChevronDown} class="section-chevron" />
    </summary>
    <div class="settings-content">
      {#if snapshotSettingsLoading}
        <div class="settings-note">Loading snapshot settings…</div>
      {:else}


        <div class="settings-row">
          <div>
            <h3>Capture Interval (seconds)</h3>
            <p class="muted">How often Locus captures active-window snapshots.</p>
          </div>
          <input class="settings-input" type="number" min="5" max="300" bind:value={snapshotIntervalSeconds} />
        </div>

        <div class="settings-row">
          <div>
            <h3>Retention (days)</h3>
            <p class="muted">Older encrypted snapshots are removed automatically.</p>
          </div>
          <input class="settings-input" type="number" min="1" max="365" bind:value={snapshotRetentionDays} />
        </div>

        <div class="settings-row">
          <div>
            <h3>Exclude Private Browsing</h3>
            <p class="muted">Skip Incognito/InPrivate windows automatically.</p>
          </div>
          <label class="switch">
            <input type="checkbox" bind:checked={snapshotExcludePrivate} />
            <span class="slider"></span>
          </label>
        </div>

        <div class="settings-row">
          <div>
            <h3>Capture On Window Change</h3>
            <p class="muted">Take an immediate snapshot when active app/window changes.</p>
          </div>
          <label class="switch">
            <input type="checkbox" bind:checked={snapshotCaptureOnWindowChange} />
            <span class="slider"></span>
          </label>
        </div>

        <div class="settings-row">
          <div>
            <h3>Allow Individual Snapshot Deletion</h3>
            <p class="muted">Off by default for integrity. Enable only if needed.</p>
          </div>
          <label class="switch">
            <input type="checkbox" bind:checked={snapshotAllowDelete} />
            <span class="slider"></span>
          </label>
        </div>

        <div class="d-flex justify-content-end">
          <button class="btn btn-primary apply-btn" style="min-width: 210px;" on:click={saveSnapshotSettings} disabled={snapshotSettingsSaving}>
            {snapshotSettingsSaving ? 'Applying…' : 'Apply'}
          </button>
        </div>

      {/if}
    </div>
  </details>

  <details class="settings-section" open>
    <summary>
      <div class="section-title">
        <Fa icon={faGears} class="section-icon" />
        <div>
          <h2>Security</h2>
          <p class="muted">Protect backup data in .locus_storage using admin permissions.</p>
        </div>
      </div>
      <Fa icon={faChevronDown} class="section-chevron" />
    </summary>
    <div class="settings-content">
      <div class="settings-row">
        <div>
          <h3>Admin Protection</h3>
          <p class="muted">
            When enabled, Windows ACLs restrict access to backup files. Requires admin rights.
          </p>
        </div>
        <label class="switch">
          <input
            type="checkbox"
            bind:checked={adminProtectionEnabled}
            on:change={toggleAdminProtection}
            disabled={adminProtectionLoading}
          />
          <span class="slider"></span>
        </label>
      </div>

      {#if adminProtectionLoading}
        <div class="settings-note">Applying security settings…</div>
      {/if}

      {#if adminProtectionInfo}
        <div class="settings-note">{adminProtectionInfo}</div>
      {/if}

      {#if adminProtectionError}
        <div class="settings-note text-danger">{adminProtectionError}</div>
      {/if}

      {#if !isAdminUser}
        <div class="settings-note">
          Admin mode not detected. To enable protection, reopen Locus as Administrator.
        </div>
      {/if}
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
          <p class="muted">Choose how Locus starts when you log in.</p>
        </div>
      </div>
      <Fa icon={faChevronDown} class="section-chevron" />
    </summary>
    <div class="settings-content">
      {#if runtimeSettingsLoading}
        <div class="settings-note">Loading runtime settings…</div>
      {:else}
        <div class="settings-row">
          <div>
            <h3>UI Zoom</h3>
            <p class="muted">Scale the Locus interface from 0.5x to 3.0x.</p>
          </div>
          <div class="d-flex align-items-center gap-2 zoom-control-group" style="min-width: 240px; justify-content: flex-end;">
            <button
              class="btn zoom-step-btn"
              type="button"
              on:click={() => nudgeUiZoomScale(-UI_ZOOM_STEP)}
              aria-label="Decrease UI zoom"
              disabled={runtimeSettingsSaving}
            >
              -
            </button>
            <input
              class="settings-input zoom-value-input"
              type="number"
              min={MIN_UI_ZOOM_SCALE}
              max={MAX_UI_ZOOM_SCALE}
              step={UI_ZOOM_STEP}
              bind:value={uiZoomScale}
              on:input={previewUiZoomScale}
              on:change={commitUiZoomScaleInput}
              disabled={runtimeSettingsSaving}
            />
            <button
              class="btn zoom-step-btn"
              type="button"
              on:click={() => nudgeUiZoomScale(UI_ZOOM_STEP)}
              aria-label="Increase UI zoom"
              disabled={runtimeSettingsSaving}
            >
              +
            </button>
          </div>
        </div>

        <div class="settings-row">
          <div>
            <h3>Run In Background Service Mode</h3>
            <p class="muted">When enabled, closing Locus after unlock keeps it running in tray on Linux and Windows.</p>
          </div>
          <label class="switch">
            <input type="checkbox" bind:checked={runInBackgroundService} />
            <span class="slider"></span>
          </label>
        </div>

        <div class="d-flex justify-content-end">
          <button class="btn btn-primary apply-btn" style="min-width: 210px;" on:click={saveRuntimeSettings} disabled={runtimeSettingsSaving}>
            {runtimeSettingsSaving ? 'Applying…' : 'Apply'}
          </button>
        </div>

        {#if runtimeSettingsError}
          <div class="settings-note text-danger" style="margin-top: 12px;">{runtimeSettingsError}</div>
        {/if}
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

<style>
  .zoom-control-group {
    gap: 10px;
  }

  .zoom-step-btn {
    width: 42px;
    height: 42px;
    border-radius: 12px;
    border: 1px solid var(--border-subtle);
    background: linear-gradient(180deg, var(--surface-elevated), var(--surface));
    color: var(--text-primary);
    font-size: 1.2rem;
    font-weight: 600;
    line-height: 1;
    box-shadow: 0 10px 18px rgba(15, 23, 42, 0.08);
    transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
  }

  .zoom-step-btn:hover:not(:disabled) {
    transform: translateY(-1px);
    border-color: rgba(59, 130, 246, 0.5);
    box-shadow: 0 12px 24px rgba(37, 99, 235, 0.18);
  }

  .zoom-step-btn:disabled {
    opacity: 0.65;
    transform: none;
    box-shadow: none;
  }

  .zoom-value-input {
    width: 120px;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }

  .apply-btn {
    border-radius: 12px;
    font-weight: 600;
    letter-spacing: 0.02em;
    box-shadow: 0 12px 24px rgba(37, 99, 235, 0.24);
  }

  .apply-btn:hover:not(:disabled) {
    transform: translateY(-1px);
  }

  .apply-btn:disabled {
    box-shadow: none;
  }
</style>
