<script>
  import { onMount } from 'svelte';
  import { showMessage, askQuestion } from '../dialogStore.js';
  import {
    getSecuritySettings,
    setSecuritySettings,
    getTrackingExclusions,
    setTrackingExclusions,
    getSnapshotSettings,
    updateSnapshotSettings
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
  let startupMode = 'startup';
  let mediaQuery;

  let snapshotSettingsLoading = false;
  let snapshotSettingsSaving = false;
  let snapshotSettingsError = '';
  let snapshotIntervalSeconds = 10;
  let snapshotRetentionDays = 10;
  let snapshotExcludePrivate = true;
  let snapshotAllowDelete = false;
  let snapshotNlpAlwaysOn = false;
  let snapshotVaultInfo = '';


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

    loadSecuritySettings();
    loadTrackingExclusions();
    loadSnapshotSettings();

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
      snapshotAllowDelete = !!data.allow_individual_delete;
      snapshotNlpAlwaysOn = !!data.nlp_always_on;
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
        allow_individual_delete: !!snapshotAllowDelete,
        nlp_always_on: !!snapshotNlpAlwaysOn
      });
    } catch (e) {
      snapshotSettingsError = e.message || 'Failed to save snapshot settings.';
    } finally {
      snapshotSettingsSaving = false;
    }
  };


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
          <p class="muted">Tune interval, retention, privacy, and local NLP behavior.</p>
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
            <h3>Allow Individual Snapshot Deletion</h3>
            <p class="muted">Off by default for integrity. Enable only if needed.</p>
          </div>
          <label class="switch">
            <input type="checkbox" bind:checked={snapshotAllowDelete} />
            <span class="slider"></span>
          </label>
        </div>

        <div class="settings-row">
          <div>
            <h3>Always-On NLP Mode</h3>
            <p class="muted">Off by default to save CPU/battery. Query mode remains on-demand.</p>
          </div>
          <label class="switch">
            <input type="checkbox" bind:checked={snapshotNlpAlwaysOn} />
            <span class="slider"></span>
          </label>
        </div>

        <div class="d-flex justify-content-end">
          <button class="btn btn-primary" on:click={saveSnapshotSettings} disabled={snapshotSettingsSaving}>
            {snapshotSettingsSaving ? 'Saving…' : 'Save Snapshot Settings'}
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
          Admin mode not detected. To enable protection, reopen LOCUS as Administrator.
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
