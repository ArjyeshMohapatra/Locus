<svelte:options runes={false} />
<script>
  import { onDestroy, onMount } from 'svelte';
  import { askForText, askQuestion, showMessage } from '../dialogStore.js';
  import {
    getSecuritySettings,
    setSecuritySettings,
    getWatchedPaths,
    getTrackingExclusions,
    setTrackingExclusions,
    getSnapshotSettings,
    updateSnapshotSettings,
    deleteAllSnapshots,
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
  let watchedProjects = [];
  let selectedProjectScope = '';
  let canAddProjectExclusion = false;
  let newExclusion = '';
  let exclusionsLoading = false;
  let exclusionsSaving = false;
  let exclusionsError = '';
  let exclusionsMigrationInFlight = false;
  const exclusionsMigrationCompletedScopes = new Set();

  const PROJECT_SCOPE_PREFIX = '@project=';
  const PROJECT_SCOPE_SEPARATOR = '::';

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
  let snapshotCount = 0;
  let snapshotBaseline = null;
  let snapshotDeleteAllRunning = false;

  let runtimeSettingsLoading = false;
  let runtimeSettingsSaving = false;
  let runtimeSettingsError = '';
  let runInBackgroundService = true;
  let uiZoomScale = 1;
  let fontZoomScale = 1;
  let shareCrashDiagnostics = false;
  let runtimeBaseline = null;
  let snapshotSettingsDirty = false;
  let runtimeSettingsDirty = false;
  const MIN_UI_ZOOM_SCALE = 0.5;
  const MAX_UI_ZOOM_SCALE = 3;
  const UI_ZOOM_STEP = 0.05;
  const MIN_FONT_ZOOM_SCALE = 0.8;
  const MAX_FONT_ZOOM_SCALE = 1.5;
  const FONT_ZOOM_STEP = 0.05;
  const SETTINGS_AUTO_SAVE_DEBOUNCE_MS = 550;

  let snapshotAutoSaveTimer = null;
  let runtimeAutoSaveTimer = null;
  let snapshotSettingsInitialized = false;
  let runtimeSettingsInitialized = false;

  const normalizeIntegerSetting = (value, fallback, min, max) => {
    const parsed = Number.parseInt(String(value), 10);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(max, Math.max(min, parsed));
  };

  const normalizeDecimalSetting = (value, fallback, min, max) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    const clamped = Math.min(max, Math.max(min, parsed));
    return Number(clamped.toFixed(2));
  };

  const snapshotSettingsState = () => ({
    interval_seconds: normalizeIntegerSetting(snapshotIntervalSeconds, 10, 5, 300),
    retention_days: normalizeIntegerSetting(snapshotRetentionDays, 10, 1, 365),
    exclude_private_browsing: !!snapshotExcludePrivate,
    capture_on_window_change: !!snapshotCaptureOnWindowChange,
    allow_individual_delete: !!snapshotAllowDelete
  });

  const runtimeSettingsState = () => ({
    run_in_background_service: !!runInBackgroundService,
    ui_zoom_scale: normalizeDecimalSetting(uiZoomScale, 1, MIN_UI_ZOOM_SCALE, MAX_UI_ZOOM_SCALE),
    font_zoom_scale: normalizeDecimalSetting(fontZoomScale, 1, MIN_FONT_ZOOM_SCALE, MAX_FONT_ZOOM_SCALE),
    share_crash_diagnostics: !!shareCrashDiagnostics
  });

  const statesMatch = (left, right) =>
    JSON.stringify(left || {}) === JSON.stringify(right || {});

  const isSnapshotSettingsDirtyNow = () => (
    snapshotBaseline
      ? !statesMatch(snapshotBaseline, snapshotSettingsState())
      : false
  );

  const isRuntimeSettingsDirtyNow = () => (
    runtimeBaseline
      ? !statesMatch(runtimeBaseline, runtimeSettingsState())
      : false
  );

  $: snapshotSettingsDirty = isSnapshotSettingsDirtyNow();

  $: runtimeSettingsDirty = isRuntimeSettingsDirtyNow();

  const clearSnapshotAutoSaveTimer = () => {
    if (snapshotAutoSaveTimer) {
      clearTimeout(snapshotAutoSaveTimer);
      snapshotAutoSaveTimer = null;
    }
  };

  const clearRuntimeAutoSaveTimer = () => {
    if (runtimeAutoSaveTimer) {
      clearTimeout(runtimeAutoSaveTimer);
      runtimeAutoSaveTimer = null;
    }
  };

  const scheduleSnapshotSettingsAutoSave = () => {
    if (
      !snapshotSettingsInitialized
      || snapshotSettingsLoading
      || snapshotSettingsSaving
      || snapshotDeleteAllRunning
      || !isSnapshotSettingsDirtyNow()
    ) {
      return;
    }

    clearSnapshotAutoSaveTimer();
    snapshotAutoSaveTimer = setTimeout(() => {
      snapshotAutoSaveTimer = null;
      void saveSnapshotSettings({ silent: true });
    }, SETTINGS_AUTO_SAVE_DEBOUNCE_MS);
  };

  const scheduleRuntimeSettingsAutoSave = () => {
    if (
      !runtimeSettingsInitialized
      || runtimeSettingsLoading
      || runtimeSettingsSaving
      || !isRuntimeSettingsDirtyNow()
    ) {
      return;
    }

    clearRuntimeAutoSaveTimer();
    runtimeAutoSaveTimer = setTimeout(() => {
      runtimeAutoSaveTimer = null;
      void saveRuntimeSettings({ silent: true });
    }, SETTINGS_AUTO_SAVE_DEBOUNCE_MS);
  };

  $: if (
    !snapshotSettingsInitialized
    || snapshotSettingsLoading
    || snapshotSettingsSaving
    || snapshotDeleteAllRunning
    || !isSnapshotSettingsDirtyNow()
  ) {
    clearSnapshotAutoSaveTimer();
  } else {
    scheduleSnapshotSettingsAutoSave();
  }

  $: if (
    !runtimeSettingsInitialized
    || runtimeSettingsLoading
    || runtimeSettingsSaving
    || !isRuntimeSettingsDirtyNow()
  ) {
    clearRuntimeAutoSaveTimer();
  } else {
    scheduleRuntimeSettingsAutoSave();
  }

  const flushPendingSettingsAutoSave = () => {
    clearSnapshotAutoSaveTimer();
    clearRuntimeAutoSaveTimer();

    if (
      snapshotSettingsInitialized
      && !snapshotSettingsLoading
      && !snapshotSettingsSaving
      && !snapshotDeleteAllRunning
      && isSnapshotSettingsDirtyNow()
    ) {
      void saveSnapshotSettings({ silent: true });
    }

    if (
      runtimeSettingsInitialized
      && !runtimeSettingsLoading
      && !runtimeSettingsSaving
      && isRuntimeSettingsDirtyNow()
    ) {
      void saveRuntimeSettings({ silent: true });
    }
  };

  const persistSnapshotSettingsNow = () => {
    if (!snapshotSettingsInitialized || snapshotSettingsLoading || snapshotDeleteAllRunning) {
      return;
    }
    void saveSnapshotSettings({ silent: true });
  };

  const persistRuntimeSettingsNow = () => {
    if (!runtimeSettingsInitialized || runtimeSettingsLoading) {
      return;
    }
    void saveRuntimeSettings({ silent: true });
  };


  const toggleGc = () => {
    gcEnabled = !gcEnabled;
  };

  const normalizeProjectScope = (value) =>
    String(value || '')
      .trim()
      .replace(/\\/g, '/')
      .replace(/\/+$/, '');

  const getProjectNameFromPath = (value) => {
    const normalized = normalizeProjectScope(value);
    if (!normalized) return 'Project';
    const parts = normalized.split('/').filter(Boolean);
    return parts.at(-1) || normalized;
  };

  const isProjectScopedRule = (value) => {
    const candidate = String(value || '').trim();
    return (
      candidate.startsWith(PROJECT_SCOPE_PREFIX)
      || candidate.startsWith(`!${PROJECT_SCOPE_PREFIX}`)
    );
  };

  const scopeRuleForProject = (rawRule, projectScope) => {
    const candidate = String(rawRule || '').trim();
    if (!candidate) return '';
    if (isProjectScopedRule(candidate)) return candidate;

    const normalizedScope = normalizeProjectScope(projectScope);
    if (!normalizedScope) {
      return candidate;
    }

    if (candidate.startsWith('!')) {
      const rule = candidate.slice(1).trim();
      if (!rule) return '';
      return `!${PROJECT_SCOPE_PREFIX}${normalizedScope}${PROJECT_SCOPE_SEPARATOR}${rule}`;
    }

    return `${PROJECT_SCOPE_PREFIX}${normalizedScope}${PROJECT_SCOPE_SEPARATOR}${candidate}`;
  };

  const decodeScopedExclusion = (value) => {
    const original = String(value || '').trim();
    if (!original) {
      return {
        scopePath: '',
        rule: '',
        displayRule: ''
      };
    }

    let body = original;
    let isNegated = false;
    if (body.startsWith('!')) {
      isNegated = true;
      body = body.slice(1).trim();
    }

    let scopePath = '';
    let rule = body;

    if (body.startsWith(PROJECT_SCOPE_PREFIX)) {
      const scopedPayload = body.slice(PROJECT_SCOPE_PREFIX.length);
      const separatorIndex = scopedPayload.indexOf(PROJECT_SCOPE_SEPARATOR);
      if (separatorIndex > 0) {
        scopePath = normalizeProjectScope(scopedPayload.slice(0, separatorIndex));
        rule = scopedPayload
          .slice(separatorIndex + PROJECT_SCOPE_SEPARATOR.length)
          .trim();
      }
    }

    const displayRule = isNegated ? `!${rule}` : rule;
    return {
      scopePath,
      rule,
      displayRule
    };
  };

  const getKnownProjectScopes = () => {
    const watchedScopes = watchedProjects
      .map((project) => normalizeProjectScope(project?.path))
      .filter(Boolean);

    const scopedFromRules = customExclusions
      .map((entry) => normalizeProjectScope(decodeScopedExclusion(entry).scopePath))
      .filter(Boolean);

    return Array.from(new Set([...watchedScopes, ...scopedFromRules]));
  };

  const getLegacyMigrationTargetScope = () => {
    const normalizedSelectedScope = normalizeProjectScope(selectedProjectScope);
    if (normalizedSelectedScope) {
      return normalizedSelectedScope;
    }

    const knownScopes = getKnownProjectScopes();
    if (knownScopes.length === 1) {
      return knownScopes[0];
    }

    return '';
  };

  const resolveProjectGroupLabel = (scopePath) => {
    if (!scopePath) return 'Project';
    const normalizedScope = normalizeProjectScope(scopePath);
    const match = watchedProjects.find(
      (project) => normalizeProjectScope(project?.path) === normalizedScope
    );
    return getProjectNameFromPath(match?.path || normalizedScope);
  };

  $: groupedCustomExclusions = (() => {
    const groups = new Map();
    const knownScopes = getKnownProjectScopes();
    const fallbackScope = knownScopes.length === 1 ? knownScopes[0] : '';

    customExclusions.forEach((entry, index) => {
      const decoded = decodeScopedExclusion(entry);
      const rule = decoded.rule || String(entry || '').trim();
      if (!rule) {
        return;
      }

      const normalizedScope = normalizeProjectScope(decoded.scopePath);
      const resolvedScope = normalizedScope || fallbackScope;
      if (!resolvedScope) {
        return;
      }
      const groupKey = resolvedScope;

      if (!groups.has(groupKey)) {
        groups.set(groupKey, {
          groupKey,
          scopePath: resolvedScope,
          projectName: resolveProjectGroupLabel(resolvedScope),
          items: []
        });
      }

      groups.get(groupKey).items.push({
        index,
        value: entry,
        displayValue: decoded.displayRule || rule
      });
    });

    return Array.from(groups.values()).sort((left, right) => {
      return left.projectName.localeCompare(right.projectName);
    });
  })();

  $: {
    const knownScopes = getKnownProjectScopes();
    const normalizedSelectedScope = normalizeProjectScope(selectedProjectScope);
    if (knownScopes.length === 0) {
      if (normalizedSelectedScope) {
        selectedProjectScope = '';
      }
    } else if (!knownScopes.includes(normalizedSelectedScope)) {
      selectedProjectScope = knownScopes[0];
    }
  }

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
    loadWatchedProjects();
    loadTrackingExclusions();
    loadSnapshotSettings();
    loadRuntimeSettings();

    return () => {
      flushPendingSettingsAutoSave();
      if (mediaQuery.removeEventListener) {
        mediaQuery.removeEventListener('change', handleSystemChange);
      } else {
        mediaQuery.removeListener(handleSystemChange);
      }
    };
  });

  onDestroy(() => {
    flushPendingSettingsAutoSave();
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

  const loadWatchedProjects = async () => {
    try {
      const data = await getWatchedPaths();
      watchedProjects = Array.isArray(data) ? data : [];
      const normalizedScopes = watchedProjects
        .map((project) => normalizeProjectScope(project?.path))
        .filter(Boolean);

      if (normalizedScopes.length > 0) {
        if (!normalizedScopes.includes(normalizeProjectScope(selectedProjectScope))) {
          selectedProjectScope = normalizedScopes[0];
        }
      } else {
        const knownScopes = getKnownProjectScopes();
        selectedProjectScope = knownScopes[0] || '';
      }
    } catch {
      watchedProjects = [];
      const knownScopes = getKnownProjectScopes();
      selectedProjectScope = knownScopes[0] || '';
    }
  };

  $: canAddProjectExclusion = watchedProjects.length > 0
    && !!normalizeProjectScope(selectedProjectScope || watchedProjects[0]?.path);

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
    if (!canAddProjectExclusion) {
      return;
    }
    const trimmed = newExclusion.trim();
    if (!trimmed) return;
    const targetScope = normalizeProjectScope(selectedProjectScope)
      || normalizeProjectScope(watchedProjects[0]?.path);
    if (!targetScope) {
      return;
    }
    const scopedRule = scopeRuleForProject(trimmed, targetScope);
    if (!scopedRule) {
      newExclusion = '';
      return;
    }
    if (customExclusions.includes(scopedRule)) {
      newExclusion = '';
      return;
    }
    const next = [...customExclusions, scopedRule];
    newExclusion = '';
    await persistExclusions(next);
  };

  $: {
    const migrationTargetScope = getLegacyMigrationTargetScope();

    if (
      !migrationTargetScope
      || exclusionsLoading
      || exclusionsSaving
      || exclusionsMigrationInFlight
      || exclusionsMigrationCompletedScopes.has(migrationTargetScope)
      || customExclusions.length === 0
    ) {
      // no-op
    } else {
      const hasUnscoped = customExclusions.some((entry) => !decodeScopedExclusion(entry).scopePath);
      if (hasUnscoped) {
        exclusionsMigrationInFlight = true;
        const migrated = customExclusions
          .map((entry) => {
            const decoded = decodeScopedExclusion(entry);
            if (decoded.scopePath) {
              return entry;
            }
            return scopeRuleForProject(decoded.displayRule || decoded.rule || entry, migrationTargetScope);
          })
          .filter(Boolean);

        const uniqueMigrated = Array.from(new Set(migrated));
        void persistExclusions(uniqueMigrated).finally(() => {
          exclusionsMigrationCompletedScopes.add(migrationTargetScope);
          exclusionsMigrationInFlight = false;
        });
      } else {
        exclusionsMigrationCompletedScopes.add(migrationTargetScope);
      }
    }
  }

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
    snapshotSettingsInitialized = false;
    snapshotSettingsLoading = true;
    snapshotSettingsError = '';
    try {
      const data = await getSnapshotSettings();
      snapshotIntervalSeconds = data.interval_seconds ?? 10;
      snapshotRetentionDays = data.retention_days ?? 10;
      snapshotExcludePrivate = !!data.exclude_private_browsing;
      snapshotCaptureOnWindowChange = data.capture_on_window_change ?? true;
      snapshotAllowDelete = !!data.allow_individual_delete;
      snapshotCount = Math.max(Number(data.snapshot_count) || 0, 0);
      snapshotBaseline = snapshotSettingsState();
    } catch (e) {
      snapshotSettingsError = e.message || 'Failed to load snapshot settings.';
    } finally {
      snapshotSettingsLoading = false;
      snapshotSettingsInitialized = true;
    }
  };

  const saveSnapshotSettings = async ({ silent = true } = {}) => {
    if (!isSnapshotSettingsDirtyNow()) return;

    snapshotSettingsSaving = true;
    snapshotSettingsError = '';
    try {
      const saved = await updateSnapshotSettings(snapshotSettingsState());
      snapshotIntervalSeconds = saved.interval_seconds ?? snapshotIntervalSeconds;
      snapshotRetentionDays = saved.retention_days ?? snapshotRetentionDays;
      snapshotExcludePrivate = saved.exclude_private_browsing ?? snapshotExcludePrivate;
      snapshotCaptureOnWindowChange =
        saved.capture_on_window_change ?? snapshotCaptureOnWindowChange;
      snapshotAllowDelete = saved.allow_individual_delete ?? snapshotAllowDelete;
      snapshotCount = Math.max(Number(saved.snapshot_count) || snapshotCount, 0);
      snapshotBaseline = snapshotSettingsState();
      if (!silent) {
        await showMessage(
          'Snapshot settings applied successfully.',
          'Settings Updated',
          'info',
          { messageScale: 1.15 }
        );
      }
    } catch (e) {
      snapshotSettingsError = e.message || 'Failed to apply snapshot settings.';
    } finally {
      snapshotSettingsSaving = false;
    }
  };

  const deleteAllStoredSnapshots = async () => {
    if (snapshotCount <= 0 || snapshotDeleteAllRunning) return;

    snapshotSettingsError = '';

    const confirmed = await askQuestion(
      'This permanently deletes all stored snapshots and timeline images. This action cannot be undone. Continue?',
      'Delete All Stored Snapshots',
      {
        type: 'warning',
        okLabel: 'Continue',
        cancelLabel: 'Cancel'
      }
    );
    if (!confirmed) return;

    const passphrase = await askForText(
      'Enter your master password or recovery password to confirm deletion of all snapshots.',
      'Confirm Deletion',
      {
        type: 'warning',
        okLabel: 'Delete All',
        cancelLabel: 'Cancel',
        inputLabel: 'Master Password or Recovery Password',
        placeholder: 'Enter credential',
        maxLength: 256
      }
    );
    if (passphrase == null) return;

    const cleanedPassphrase = String(passphrase).trim();
    if (!cleanedPassphrase) {
      snapshotSettingsError = 'Password or recovery password is required to delete snapshots.';
      return;
    }

    snapshotDeleteAllRunning = true;
    let successMessage = '';
    try {
      const result = await deleteAllSnapshots(cleanedPassphrase);
      snapshotCount = 0;
      successMessage = `Deleted ${result.deleted_snapshots ?? 0} snapshots and cleared ${result.deleted_images ?? 0} stored images.`;
    } catch (e) {
      snapshotSettingsError = e.message || 'Failed to delete stored snapshots.';
    } finally {
      snapshotDeleteAllRunning = false;
    }

    if (successMessage) {
      await showMessage(
        successMessage,
        'Snapshots Deleted',
        'info',
        { messageScale: 1.15 }
      );
    }
  };

  const loadRuntimeSettings = async () => {
    runtimeSettingsInitialized = false;
    runtimeSettingsLoading = true;
    runtimeSettingsError = '';
    try {
      const data = await getRuntimeSettings();
      runInBackgroundService = data.run_in_background_service ?? true;
      const parsedZoom = Number(data.ui_zoom_scale ?? 1);
      uiZoomScale = Number.isFinite(parsedZoom)
        ? Math.min(MAX_UI_ZOOM_SCALE, Math.max(MIN_UI_ZOOM_SCALE, parsedZoom))
        : 1;
      const parsedFontZoom = Number(data.font_zoom_scale ?? 1);
      fontZoomScale = Number.isFinite(parsedFontZoom)
        ? Math.min(MAX_FONT_ZOOM_SCALE, Math.max(MIN_FONT_ZOOM_SCALE, parsedFontZoom))
        : 1;
      shareCrashDiagnostics = !!data.share_crash_diagnostics;
      runtimeBaseline = runtimeSettingsState();
    } catch (e) {
      runtimeSettingsError = e.message || 'Failed to load runtime settings.';
    } finally {
      runtimeSettingsLoading = false;
      runtimeSettingsInitialized = true;
    }
  };

  const emitRuntimeSettingsChange = () => {
    window.dispatchEvent(
      new CustomEvent('locus-runtime-settings-change', {
        detail: {
          runInBackgroundService,
          uiZoomScale,
          fontZoomScale,
          shareCrashDiagnostics
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

  const clampFontZoomScale = (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return 1;
    return Math.min(MAX_FONT_ZOOM_SCALE, Math.max(MIN_FONT_ZOOM_SCALE, parsed));
  };

  const normalizeFontZoomScale = (value) =>
    Number(clampFontZoomScale(value).toFixed(2));

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

  const nudgeFontZoomScale = async (delta) => {
    fontZoomScale = normalizeFontZoomScale(Number(fontZoomScale) + delta);
    previewUiZoomScale();
    await saveRuntimeSettings({ silent: true });
  };

  const commitFontZoomScaleInput = async () => {
    fontZoomScale = normalizeFontZoomScale(fontZoomScale);
    previewUiZoomScale();
    await saveRuntimeSettings({ silent: true });
  };

  const saveRuntimeSettings = async ({ silent = true } = {}) => {
    if (!isRuntimeSettingsDirtyNow()) return;

    runtimeSettingsSaving = true;
    runtimeSettingsError = '';
    try {
      const data = await updateRuntimeSettings(runtimeSettingsState());
      runInBackgroundService = data.run_in_background_service ?? runInBackgroundService;
      const parsedZoom = Number(data.ui_zoom_scale ?? uiZoomScale);
      uiZoomScale = Number.isFinite(parsedZoom)
        ? Math.min(MAX_UI_ZOOM_SCALE, Math.max(MIN_UI_ZOOM_SCALE, parsedZoom))
        : uiZoomScale;
      const parsedFontZoom = Number(data.font_zoom_scale ?? fontZoomScale);
      fontZoomScale = Number.isFinite(parsedFontZoom)
        ? Math.min(MAX_FONT_ZOOM_SCALE, Math.max(MIN_FONT_ZOOM_SCALE, parsedFontZoom))
        : fontZoomScale;
      shareCrashDiagnostics = !!data.share_crash_diagnostics;
      runtimeBaseline = runtimeSettingsState();
      emitRuntimeSettingsChange();
      if (!silent) {
        void showMessage(
          'Runtime preferences applied successfully.',
          'Settings Updated',
          'info',
          { messageScale: 1.15 }
        );
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
        <details class="settings-note default-exclusion-details">
          <summary class="default-exclusion-summary">
            <span>Default exclusion:</span>
            <Fa icon={faChevronDown} class="default-exclusion-chevron" />
          </summary>
          <div class="chip-list" style="margin-top:8px;">
            {#each excludedFolders as folder (folder)}
              <span class="chip is-readonly">{folder}</span>
            {/each}
          </div>
        </details>

        <div class="filter-input" style="margin-top: 12px;">
          <select
            class="settings-input exclusion-project-select"
            bind:value={selectedProjectScope}
            disabled={exclusionsSaving || watchedProjects.length === 0}
          >
            {#if watchedProjects.length === 0}
              <option value="">No watched projects</option>
            {:else}
              {#each watchedProjects as project (project.id ?? project.path)}
                <option value={normalizeProjectScope(project.path)}>{getProjectNameFromPath(project.path)}</option>
              {/each}
            {/if}
          </select>
          <input
            type="text"
            placeholder="Add a pattern for selected project (e.g., node_modules)"
            bind:value={newExclusion}
            on:keydown={(e) => e.key === 'Enter' && addCustomExclusion()}
            disabled={exclusionsSaving || !canAddProjectExclusion}
          />
          <button class="btn btn-primary" on:click={addCustomExclusion} disabled={exclusionsSaving || !canAddProjectExclusion}>
            {exclusionsSaving ? 'Saving…' : 'Add'}
          </button>
        </div>

        {#if !canAddProjectExclusion && groupedCustomExclusions.length === 0}
          <div class="settings-note" style="margin-top: 12px;">Add a watched project first to create project exclusions.</div>
        {/if}

        <details class="settings-note default-exclusion-details" style="margin-top: 12px;">
          <summary class="default-exclusion-summary">
            <span>Custom exclusions:</span>
            <Fa icon={faChevronDown} class="default-exclusion-chevron" />
          </summary>
          <div style="padding: 0 12px 12px; display: flex; flex-direction: column; gap: 12px;">
            {#if groupedCustomExclusions.length === 0}
              <div class="settings-note" style="margin: 0;">No custom exclusions yet.</div>
            {:else}
              <div class="custom-exclusion-groups">
                {#each groupedCustomExclusions as group (group.groupKey)}
                  <details class="custom-exclusion-group">
                    <summary class="custom-exclusion-group-header">
                      <div class="custom-exclusion-group-title">
                        <span class="custom-exclusion-project-name">{group.projectName}</span>
                        {#if group.scopePath}
                          <span class="custom-exclusion-project-path">{group.scopePath}</span>
                        {/if}
                      </div>
                      <div class="custom-exclusion-group-meta">
                        <span class="badge-soft badge-soft-secondary">{group.items.length}</span>
                        <Fa icon={faChevronDown} class="custom-exclusion-group-chevron" />
                      </div>
                    </summary>
                    <div class="chip-list custom-exclusion-group-chips">
                      {#each group.items as item (`${item.value}-${item.index}`)}
                        <span class="chip">
                          {item.displayValue}
                          <button class="chip-remove" on:click={() => removeCustomExclusion(item.index)} disabled={exclusionsSaving}>
                            ×
                          </button>
                        </span>
                      {/each}
                    </div>
                  </details>
                {/each}
              </div>
            {/if}
          </div>
        </details>
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
          <input
            class="settings-input"
            type="number"
            min="5"
            max="300"
            bind:value={snapshotIntervalSeconds}
            on:change={persistSnapshotSettingsNow}
          />
        </div>

        <div class="settings-row">
          <div>
            <h3>Retention (days)</h3>
            <p class="muted">Older encrypted snapshots are removed automatically.</p>
          </div>
          <input
            class="settings-input"
            type="number"
            min="1"
            max="365"
            bind:value={snapshotRetentionDays}
            on:change={persistSnapshotSettingsNow}
          />
        </div>

        <div class="settings-row">
          <div>
            <h3>Exclude Private Browsing</h3>
            <p class="muted">Skip Incognito/InPrivate windows automatically.</p>
          </div>
          <label class="switch">
            <input
              type="checkbox"
              bind:checked={snapshotExcludePrivate}
              on:change={persistSnapshotSettingsNow}
            />
            <span class="slider"></span>
          </label>
        </div>

        <div class="settings-row">
          <div>
            <h3>Capture On Window Change</h3>
            <p class="muted">Take an immediate snapshot when active app/window changes.</p>
          </div>
          <label class="switch">
            <input
              type="checkbox"
              bind:checked={snapshotCaptureOnWindowChange}
              on:change={persistSnapshotSettingsNow}
            />
            <span class="slider"></span>
          </label>
        </div>

        <div class="settings-row">
          <div>
            <h3>Allow Individual Snapshot Deletion</h3>
            <p class="muted">Off by default for integrity. Enable only if needed.</p>
          </div>
          <label class="switch">
            <input
              type="checkbox"
              bind:checked={snapshotAllowDelete}
              on:change={persistSnapshotSettingsNow}
            />
            <span class="slider"></span>
          </label>
        </div>

        <div class="settings-row">
          <div>
            <h3>Delete All Stored Snapshots</h3>
            <p class="muted">Permanently removes all snapshot history and images. Requires unlocked vault and credential confirmation.</p>
          </div>
          <button
            class="btn btn-danger apply-btn"
            on:click={deleteAllStoredSnapshots}
            disabled={snapshotDeleteAllRunning || snapshotSettingsSaving || snapshotCount <= 0}
          >
            {snapshotDeleteAllRunning ? 'Deleting…' : 'Delete All'}
          </button>
        </div>

        {#if snapshotSettingsError}
          <div class="settings-note text-danger" style="margin-top: 12px;">{snapshotSettingsError}</div>
        {/if}

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
              on:blur={commitUiZoomScaleInput}
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
            <h3>Font Zoom</h3>
            <p class="muted">Scale text from 0.8x to 1.5x for better readability.</p>
          </div>
          <div class="d-flex align-items-center gap-2 zoom-control-group" style="min-width: 240px; justify-content: flex-end;">
            <button
              class="btn zoom-step-btn"
              type="button"
              on:click={() => nudgeFontZoomScale(-FONT_ZOOM_STEP)}
              aria-label="Decrease font zoom"
              disabled={runtimeSettingsSaving}
            >
              -
            </button>
            <input
              class="settings-input zoom-value-input"
              type="number"
              min={MIN_FONT_ZOOM_SCALE}
              max={MAX_FONT_ZOOM_SCALE}
              step={FONT_ZOOM_STEP}
              bind:value={fontZoomScale}
              on:input={previewUiZoomScale}
              on:change={commitFontZoomScaleInput}
              on:blur={commitFontZoomScaleInput}
              disabled={runtimeSettingsSaving}
            />
            <button
              class="btn zoom-step-btn"
              type="button"
              on:click={() => nudgeFontZoomScale(FONT_ZOOM_STEP)}
              aria-label="Increase font zoom"
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
            <input
              type="checkbox"
              bind:checked={runInBackgroundService}
              on:change={persistRuntimeSettingsNow}
            />
            <span class="slider"></span>
          </label>
        </div>

        <div class="settings-row">
          <div>
            <h3>Share Crash Diagnostics</h3>
            <p class="muted">Allow detailed crash reports to be forwarded to developers.</p>
          </div>
          <label class="switch">
            <input
              type="checkbox"
              bind:checked={shareCrashDiagnostics}
              on:change={persistRuntimeSettingsNow}
            />
            <span class="slider"></span>
          </label>
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
    border-radius: 10px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-primary);
    font-size: 1.05rem;
    font-weight: 600;
    line-height: 1;
    box-shadow: none;
    transition: none;
  }

  .zoom-step-btn:disabled {
    opacity: 0.65;
    box-shadow: none;
  }

  .zoom-value-input {
    width: 120px;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }

  .apply-btn {
    border-radius: var(--ui-radius-control, 999px);
    font-weight: 700;
    letter-spacing: 0.01em;
    box-shadow: none;
    min-width: 0;
    width: auto;
    padding-inline: 1.2rem;
  }

  .apply-btn:hover:not(:disabled) {
    transform: none;
  }

  .apply-btn:disabled {
    box-shadow: none;
  }
</style>
