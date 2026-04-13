<svelte:options runes={false} />
<script>
  import { onMount } from 'svelte';
  import {
    createCheckpointSession,
    diffCheckpointSessions,
    getFileVersionContent,
    getWatchedPaths,
    listCheckpointSessions,
    renameCheckpointSession,
    restoreCheckpointSession
  } from '../api.js';
  import { DATE_TIME_FORMATS, formatDateTime } from './dateTime.js';
  import { askForText, askQuestion, showMessage } from '../dialogStore.js';

  const TAB_CREATE = 'create';
  const TAB_HISTORY = 'history';
  const TAB_DIFF = 'diff';
  const TAB_RESTORE = 'restore';

  const topTabs = [
    { id: TAB_CREATE, label: 'Create' },
    { id: TAB_HISTORY, label: 'History' },
    { id: TAB_DIFF, label: 'Diff Explorer' },
    { id: TAB_RESTORE, label: 'Restore' }
  ];

  let activeTab = TAB_DIFF;

  let watchedPaths = [];
  let selectedWatchedPath = '';

  let createScope = 'full_folder';
  let createName = '';
  let createFilePathsInput = '';
  let creating = false;

  let sessions = [];
  let loadingSessions = false;
  let renamingSessionId = '';

  let fromSessionId = '';
  let toSessionId = '';
  let includeUnchanged = false;
  let diffLoading = false;
  let diffResult = null;
  let selectedDiffFilePath = '';
  let modifiedItems = [];
  let selectedDiffItem = null;
  let totalChangedFiles = 0;
  let diffTreeRows = [];
  let expandedDiffFolders = new Set();
  let selectedDiffContentLoading = false;
  let selectedDiffContentError = '';
  let selectedDiffContentKey = '';
  let selectedDiffBeforeState = null;
  let selectedDiffAfterState = null;
  let selectedDiffBeforeLines = [];
  let selectedDiffAfterLines = [];
  let selectedDiffBeforeHighlights = new Set();
  let selectedDiffAfterHighlights = new Set();
  let diffContentLoadToken = 0;
  let beforeStatePaneEl;
  let afterStatePaneEl;
  let syncedScrollSuppressedPane = null;
  let syncedScrollReleaseHandle = null;
  let isDiffFilesPaneCollapsed = false;

  let restoreSessionId = '';
  let restoreDestinationRoot = '';
  let restoreConflictStrategy = 'rename';
  let restoreLoading = false;
  let restoreExecuting = false;
  let restorePreview = null;

  let pageError = '';

  const setActiveTab = (tab) => {
    activeTab = tab;
  };

  const formatTime = (value) => {
    return formatDateTime(value, DATE_TIME_FORMATS.MEDIUM_DATE_TIME);
  };

  const formatCheckpointName = (value) => {
    const name = String(value || '').trim();
    const match = name.match(/^Checkpoint\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2}):(\d{2})(?:\s*([AaPp][Mm]))?$/);
    if (!match) return name;

    const [, datePart, hh, mm, ss, suffix] = match;
    const hourRaw = Number(hh);

    if (!Number.isFinite(hourRaw)) return name;

    if (suffix) {
      const normalizedHour = hourRaw === 0 ? 12 : hourRaw;
      return `Checkpoint ${datePart} ${String(normalizedHour).padStart(2, '0')}:${mm}:${ss} ${suffix.toUpperCase()}`;
    }

    const period = hourRaw >= 12 ? 'PM' : 'AM';
    const hour12 = hourRaw % 12 || 12;
    return `Checkpoint ${datePart} ${String(hour12).padStart(2, '0')}:${mm}:${ss} ${period}`;
  };

  const toScopeLabel = (scope) => {
    if (scope === 'single_file') return 'Single File';
    if (scope === 'selected_files') return 'Selected Files';
    return 'Full Folder';
  };

  const projectNameFromPath = (path) => {
    const normalized = String(path || '').replace(/\\/g, '/').replace(/\/+$/, '');
    if (!normalized) return 'Unknown Project';
    const parts = normalized.split('/').filter(Boolean);
    return parts[parts.length - 1] || normalized;
  };

  const formatDelta = (value, prefix) => `${prefix}${Number(value || 0)}`;

  const parseFilePaths = () => {
    const lines = String(createFilePathsInput || '')
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);

    const unique = [];
    const seen = new Set();
    for (const line of lines) {
      if (seen.has(line)) continue;
      seen.add(line);
      unique.push(line);
    }
    return unique;
  };

  const getDiffReason = (reason) => {
    const key = String(reason || '').trim();
    if (!key) return 'Line diff unavailable';
    if (key === 'binary_file') return 'Binary file';
    if (key === 'file_too_large_for_line_diff') return 'File too large for line diff preview';
    if (key === 'stored_version_unavailable') return 'Stored file version not found on disk';
    if (key === 'failed_to_read_stored_version') return 'Stored version could not be read';
    if (key === 'missing_file_versions') return 'Version metadata is incomplete';
    return key.replace(/_/g, ' ');
  };

  const filePathKey = (item) => String(item?.file_path || '');
  const toInt = (value) => Number(value || 0);

  const splitContentLines = (content) => {
    const text = typeof content === 'string' ? content : '';
    if (!text) return [];
    return text.replace(/\r\n/g, '\n').split('\n');
  };

  const buildHighlightedLineSets = (item) => {
    const before = new Set();
    const after = new Set();

    const hunks = item?.line_diff?.hunks;
    if (!Array.isArray(hunks)) {
      return { before, after };
    }

    for (const hunk of hunks) {
      const fromStart = Number(hunk?.from_start || 0);
      const fromCount = Number(hunk?.from_count || 0);
      const toStart = Number(hunk?.to_start || 0);
      const toCount = Number(hunk?.to_count || 0);

      for (let i = 0; i < fromCount; i += 1) {
        before.add(fromStart + i);
      }
      for (let i = 0; i < toCount; i += 1) {
        after.add(toStart + i);
      }
    }

    return { before, after };
  };

  const resetSelectedDiffContentState = () => {
    selectedDiffContentLoading = false;
    selectedDiffContentError = '';
    selectedDiffBeforeState = null;
    selectedDiffAfterState = null;
    selectedDiffBeforeLines = [];
    selectedDiffAfterLines = [];
    selectedDiffBeforeHighlights = new Set();
    selectedDiffAfterHighlights = new Set();
  };

  const loadSelectedDiffContent = async (item, contentKey) => {
    if (!item) return;

    const fromVersionId = Number(item.from_file_version_id || 0);
    const toVersionId = Number(item.to_file_version_id || 0);
    if (!fromVersionId || !toVersionId) {
      resetSelectedDiffContentState();
      selectedDiffContentError = 'Missing version IDs for selected diff file.';
      return;
    }

    const token = ++diffContentLoadToken;
    resetSelectedDiffContentState();
    selectedDiffContentLoading = true;

    try {
      const [beforeState, afterState] = await Promise.all([
        getFileVersionContent(fromVersionId),
        getFileVersionContent(toVersionId)
      ]);

      if (token !== diffContentLoadToken || contentKey !== selectedDiffContentKey) return;

      selectedDiffBeforeState = beforeState || null;
      selectedDiffAfterState = afterState || null;

      const beforeType = String(beforeState?.type || 'text');
      const afterType = String(afterState?.type || 'text');

      selectedDiffBeforeLines = beforeType === 'text' ? splitContentLines(beforeState?.content) : [];
      selectedDiffAfterLines = afterType === 'text' ? splitContentLines(afterState?.content) : [];

      const highlights = buildHighlightedLineSets(item);
      selectedDiffBeforeHighlights = highlights.before;
      selectedDiffAfterHighlights = highlights.after;

      if (beforeStatePaneEl) beforeStatePaneEl.scrollTop = 0;
      if (afterStatePaneEl) afterStatePaneEl.scrollTop = 0;
    } catch (e) {
      if (token !== diffContentLoadToken || contentKey !== selectedDiffContentKey) return;
      resetSelectedDiffContentState();
      selectedDiffContentError = e.message || 'Failed to load full file states for this diff.';
    } finally {
      if (token === diffContentLoadToken && contentKey === selectedDiffContentKey) {
        selectedDiffContentLoading = false;
      }
    }
  };

  const syncStatePaneScroll = (source, target) => {
    if (!source || !target) return;
    if (syncedScrollSuppressedPane === source) return;

    const sourceScrollable = source.scrollHeight - source.clientHeight;
    const targetScrollable = target.scrollHeight - target.clientHeight;
    const ratio = sourceScrollable > 0 ? source.scrollTop / sourceScrollable : 0;
    const nextTop = ratio * Math.max(0, targetScrollable);

    if (Math.abs((target.scrollTop || 0) - nextTop) < 1) {
      return;
    }

    syncedScrollSuppressedPane = target;
    target.scrollTop = nextTop;

    if (syncedScrollReleaseHandle) {
      cancelAnimationFrame(syncedScrollReleaseHandle);
    }

    syncedScrollReleaseHandle = requestAnimationFrame(() => {
      syncedScrollSuppressedPane = null;
      syncedScrollReleaseHandle = null;
    });
  };

  const onBeforeStateScroll = () => {
    syncStatePaneScroll(beforeStatePaneEl, afterStatePaneEl);
  };

  const onAfterStateScroll = () => {
    syncStatePaneScroll(afterStatePaneEl, beforeStatePaneEl);
  };

  const normalizeDiffPath = (path) =>
    String(path || '')
      .replace(/\\/g, '/')
      .replace(/\/+/g, '/')
      .replace(/^\/+/, '');

  const toDiffTree = (items) => {
    const root = new Map();

    for (const item of items) {
      const normalized = normalizeDiffPath(item?.file_path);
      if (!normalized) continue;
      const parts = normalized.split('/').filter(Boolean);
      let cursor = root;
      let folderKey = '';

      for (let i = 0; i < parts.length; i += 1) {
        const part = parts[i];
        const isLeaf = i === parts.length - 1;

        if (isLeaf) {
          cursor.set(`file:${normalized}`, {
            type: 'file',
            key: `file:${normalized}`,
            name: part,
            fullPath: item.file_path,
            item
          });
        } else {
          folderKey = folderKey ? `${folderKey}/${part}` : part;
          const existing = cursor.get(`folder:${folderKey}`);
          if (existing && existing.type === 'folder') {
            cursor = existing.children;
          } else {
            const folderNode = {
              type: 'folder',
              key: `folder:${folderKey}`,
              name: part,
              children: new Map()
            };
            cursor.set(folderNode.key, folderNode);
            cursor = folderNode.children;
          }
        }
      }
    }

    const sortNodes = (nodeMap) => {
      const nodes = Array.from(nodeMap.values());
      nodes.sort((a, b) => {
        if (a.type !== b.type) return a.type === 'folder' ? -1 : 1;
        return String(a.name).localeCompare(String(b.name), undefined, { sensitivity: 'base' });
      });

      return nodes.map((node) => {
        if (node.type !== 'folder') return node;
        return {
          ...node,
          children: sortNodes(node.children)
        };
      });
    };

    return sortNodes(root);
  };

  const collectDiffFolderKeys = (nodes, acc = new Set()) => {
    for (const node of nodes) {
      if (node.type !== 'folder') continue;
      acc.add(node.key);
      collectDiffFolderKeys(node.children, acc);
    }
    return acc;
  };

  const flattenDiffTree = (nodes, expandedFolders, depth = 0, acc = []) => {
    for (const node of nodes) {
      if (node.type === 'folder') {
        acc.push({
          kind: 'folder',
          key: node.key,
          name: node.name,
          depth,
          expanded: expandedFolders.has(node.key),
          childCount: node.children.length
        });
        if (expandedFolders.has(node.key)) {
          flattenDiffTree(node.children, expandedFolders, depth + 1, acc);
        }
      } else {
        acc.push({
          kind: 'file',
          key: node.key,
          name: node.name,
          depth,
          item: node.item,
          fullPath: node.fullPath
        });
      }
    }
    return acc;
  };

  const toggleDiffFolder = (folderKey) => {
    const next = new Set(expandedDiffFolders);
    if (next.has(folderKey)) {
      next.delete(folderKey);
    } else {
      next.add(folderKey);
    }
    expandedDiffFolders = next;
  };

  const selectDiffFile = (item) => {
    selectedDiffFilePath = filePathKey(item);
  };

  $: modifiedItems = Array.isArray(diffResult?.modified) ? diffResult.modified : [];
  $: totalChangedFiles = diffResult
    ? toInt(diffResult.summary?.added) +
      toInt(diffResult.summary?.removed) +
      toInt(diffResult.summary?.modified) +
      toInt(diffResult.summary?.renamed)
    : 0;
  $: if (modifiedItems.length === 0) {
    selectedDiffFilePath = '';
  } else if (!modifiedItems.some((item) => filePathKey(item) === selectedDiffFilePath)) {
    selectedDiffFilePath = filePathKey(modifiedItems[0]);
  }
  $: selectedDiffItem =
    modifiedItems.find((item) => filePathKey(item) === selectedDiffFilePath) || null;
  $: {
    const treeNodes = toDiffTree(modifiedItems);
    const allFolders = collectDiffFolderKeys(treeNodes);
    const nextExpanded = new Set();
    for (const key of expandedDiffFolders) {
      if (allFolders.has(key)) nextExpanded.add(key);
    }
    for (const key of allFolders) {
      if (!nextExpanded.has(key)) nextExpanded.add(key);
    }

    let changed = nextExpanded.size !== expandedDiffFolders.size;
    if (!changed) {
      for (const key of nextExpanded) {
        if (!expandedDiffFolders.has(key)) {
          changed = true;
          break;
        }
      }
    }

    const activeExpanded = changed ? nextExpanded : expandedDiffFolders;
    if (changed) {
      expandedDiffFolders = nextExpanded;
    }

    diffTreeRows = flattenDiffTree(treeNodes, activeExpanded);
  }
  $: {
    const nextContentKey = selectedDiffItem
      ? `${selectedDiffItem.file_path}|${selectedDiffItem.from_file_version_id}|${selectedDiffItem.to_file_version_id}`
      : '';

    if (!nextContentKey) {
      selectedDiffContentKey = '';
      resetSelectedDiffContentState();
    } else if (nextContentKey !== selectedDiffContentKey) {
      selectedDiffContentKey = nextContentKey;
      loadSelectedDiffContent(selectedDiffItem, nextContentKey);
    }
  }

  const loadWatched = async () => {
    const data = await getWatchedPaths();
    watchedPaths = Array.isArray(data) ? data : [];

    if (!selectedWatchedPath && watchedPaths.length > 0) {
      selectedWatchedPath = watchedPaths[0].path;
    }

    if (
      selectedWatchedPath &&
      watchedPaths.length > 0 &&
      !watchedPaths.some((row) => row.path === selectedWatchedPath)
    ) {
      selectedWatchedPath = watchedPaths[0].path;
    }
  };

  const loadSessions = async () => {
    if (!selectedWatchedPath) {
      sessions = [];
      fromSessionId = '';
      toSessionId = '';
      restoreSessionId = '';
      return;
    }

    loadingSessions = true;
    pageError = '';
    try {
      const data = await listCheckpointSessions({ watchedPath: selectedWatchedPath, limit: 200 });
      sessions = Array.isArray(data) ? data : [];

      if (sessions.length === 0) {
        fromSessionId = '';
        toSessionId = '';
        restoreSessionId = '';
        restorePreview = null;
        if (activeTab !== TAB_CREATE) {
          activeTab = TAB_CREATE;
        }
        return;
      }

      if (!sessions.some((row) => String(row.id) === String(fromSessionId))) {
        fromSessionId = String(sessions[0].id);
      }
      if (!sessions.some((row) => String(row.id) === String(toSessionId))) {
        toSessionId = String(sessions[Math.min(1, sessions.length - 1)].id);
      }
      if (!sessions.some((row) => String(row.id) === String(restoreSessionId))) {
        restoreSessionId = String(sessions[0].id);
      }
    } catch (e) {
      pageError = e.message || 'Failed to load checkpoint sessions';
      sessions = [];
      restorePreview = null;
    } finally {
      loadingSessions = false;
    }
  };

  const refreshAll = async () => {
    pageError = '';
    diffResult = null;
    restorePreview = null;

    try {
      await loadWatched();
      await loadSessions();
    } catch (e) {
      pageError = e.message || 'Failed to refresh checkpoints';
    }
  };

  const createSession = async () => {
    if (!selectedWatchedPath) {
      pageError = 'Select a watched folder first.';
      return;
    }

    creating = true;
    pageError = '';

    try {
      const payload = {
        watched_path: selectedWatchedPath,
        scope: createScope
      };

      const cleanedName = String(createName || '').trim();
      if (cleanedName) {
        payload.name = cleanedName;
      }

      if (createScope !== 'full_folder') {
        payload.file_paths = parseFilePaths();
      }

      const created = await createCheckpointSession(payload);
      await showMessage(`Checkpoint created: ${formatCheckpointName(created.name)}`, 'Checkpoint');
      await loadSessions();
      activeTab = TAB_HISTORY;

      if (createScope !== 'full_folder') {
        createFilePathsInput = '';
      }
    } catch (e) {
      pageError = e.message || 'Failed to create checkpoint';
    } finally {
      creating = false;
    }
  };

  const renameSession = async (session) => {
    const next = await askForText(
      'Enter a new name for this checkpoint.',
      'Rename Checkpoint',
      {
        type: 'question',
        okLabel: 'Save',
        cancelLabel: 'Cancel',
        inputLabel: 'Checkpoint name',
        placeholder: 'before-upgrade',
        initialValue: session.name || '',
        maxLength: 80
      }
    );
    if (next == null) return;

    const cleaned = String(next)
      .trim()
      .replace(/\s+/g, ' ');
    if (!cleaned) {
      pageError = 'Checkpoint name cannot be empty.';
      return;
    }

    pageError = '';
    renamingSessionId = String(session.id);
    try {
      await renameCheckpointSession(session.id, cleaned);
      await loadSessions();
    } catch (e) {
      pageError = e.message || 'Failed to rename checkpoint';
    } finally {
      renamingSessionId = '';
    }
  };

  const compareSessions = async () => {
    if (!selectedWatchedPath) {
      pageError = 'Select a tracked project from the header first.';
      return;
    }

    if (!fromSessionId || !toSessionId) {
      pageError = 'Pick both sessions to compare.';
      return;
    }

    if (String(fromSessionId) === String(toSessionId)) {
      pageError = 'Choose two different sessions for diff.';
      return;
    }

    diffLoading = true;
    pageError = '';
    try {
      diffResult = await diffCheckpointSessions(
        Number(fromSessionId),
        Number(toSessionId),
        includeUnchanged
      );
    } catch (e) {
      pageError = e.message || 'Failed to diff checkpoint sessions';
      diffResult = null;
    } finally {
      diffLoading = false;
    }
  };

  const buildRestorePayload = (dryRun) => ({
    destination_root: String(restoreDestinationRoot || '').trim() || null,
    conflict_strategy: restoreConflictStrategy,
    dry_run: !!dryRun
  });

  const previewRestore = async () => {
    if (!restoreSessionId) {
      pageError = 'Pick a checkpoint session for restore preview.';
      return;
    }

    restoreLoading = true;
    pageError = '';
    try {
      restorePreview = await restoreCheckpointSession(
        Number(restoreSessionId),
        buildRestorePayload(true)
      );
    } catch (e) {
      pageError = e.message || 'Failed to preview restore';
      restorePreview = null;
    } finally {
      restoreLoading = false;
    }
  };

  const executeRestore = async () => {
    if (!restoreSessionId) {
      pageError = 'Pick a checkpoint session to restore.';
      return;
    }

    if (restoreConflictStrategy === 'overwrite') {
      const confirmed = await askQuestion(
        'Overwrite strategy will replace existing files at the destination. Continue?',
        'Confirm Restore',
        { type: 'warning', okLabel: 'Overwrite', cancelLabel: 'Cancel' }
      );
      if (!confirmed) return;
    }

    restoreExecuting = true;
    pageError = '';

    try {
      const result = await restoreCheckpointSession(
        Number(restoreSessionId),
        buildRestorePayload(false)
      );
      restorePreview = result;

      await showMessage(
        `Restore complete. Restored: ${result.summary.restored}, Skipped: ${result.summary.skipped}, Failed: ${result.summary.failed}.`,
        'Checkpoint Restore'
      );
    } catch (e) {
      pageError = e.message || 'Failed to execute restore';
    } finally {
      restoreExecuting = false;
    }
  };

  const onWatchedChange = async () => {
    diffResult = null;
    restorePreview = null;
    restoreDestinationRoot = '';
    sessions = [];
    fromSessionId = '';
    toSessionId = '';
    restoreSessionId = '';
    await loadSessions();
  };

  onMount(async () => {
    await refreshAll();
  });
</script>

<section class="checkpoint-page">
  <header class="checkpoint-header">
    <div>
      <h1 class="mb-1">Checkpoint Sessions</h1>
      <p class="text-muted mb-0">Use the top tabs to focus on one workflow at a time.</p>
    </div>
    <div class="checkpoint-header-actions">
      <div class="project-picker">
        <label class="form-label fw-semibold" for="checkpoint-project-select">Tracked Project</label>
        <select
          id="checkpoint-project-select"
          class="form-select"
          bind:value={selectedWatchedPath}
          on:change={onWatchedChange}
          disabled={loadingSessions || watchedPaths.length === 0}
        >
          <option value="">Select tracked project</option>
          {#each watchedPaths as row (row.path)}
            <option value={row.path}>{projectNameFromPath(row.path)}</option>
          {/each}
        </select>
      </div>
      <button class="btn refresh-header-btn" on:click={refreshAll} disabled={loadingSessions || creating || diffLoading || restoreLoading || restoreExecuting}>
        Refresh
      </button>
    </div>
  </header>

  <nav class="checkpoint-top-nav" aria-label="Checkpoint navigation">
    {#each topTabs as tab (tab.id)}
      <button
        class="top-nav-item {activeTab === tab.id ? 'is-active' : ''}"
        type="button"
        on:click={() => setActiveTab(tab.id)}
      >
        {tab.label}
      </button>
    {/each}
  </nav>

  {#if pageError}
    <div class="alert alert-danger py-2 mb-0">{pageError}</div>
  {/if}

  <section class="checkpoint-panel">
    {#if activeTab === TAB_CREATE}
      <div class="panel-head">
        <h2>Create Checkpoint</h2>
        <span class="panel-badge">Manual Snapshot</span>
      </div>
      <div class="panel-body">
        <div class="form-grid">
          <div class="small text-muted">
            {#if selectedWatchedPath}
              Using tracked project: <span class="fw-semibold">{projectNameFromPath(selectedWatchedPath)}</span>
            {:else}
              Select a tracked project from the header to create a checkpoint.
            {/if}
          </div>

          <div class="split-row split-row-create">
            <div class="scope-field">
              <label class="form-label fw-semibold" for="checkpoint-scope">Scope</label>
              <select id="checkpoint-scope" class="form-select" bind:value={createScope}>
                <option value="full_folder">Full Folder</option>
                <option value="single_file">Single File</option>
                <option value="selected_files">Selected Files</option>
              </select>
            </div>
            <div class="label-field">
              <label class="form-label fw-semibold" for="checkpoint-name">Label</label>
              <input id="checkpoint-name" class="form-control" type="text" bind:value={createName} maxlength="80" placeholder="before-upgrade" />
            </div>
            <div class="create-action-cell">
              <button class="btn btn-primary" on:click={createSession} disabled={creating || !selectedWatchedPath}>
                {creating ? 'Creating...' : 'Create Checkpoint'}
              </button>
            </div>
          </div>

          {#if createScope !== 'full_folder'}
            <div>
              <label class="form-label fw-semibold" for="checkpoint-file-paths">Absolute File Paths</label>
              <textarea
                id="checkpoint-file-paths"
                class="form-control mono-input"
                rows="5"
                bind:value={createFilePathsInput}
                placeholder={createScope === 'single_file' ? '/abs/path/to/file.ext' : '/abs/path/to/fileA.ext\n/abs/path/to/fileB.ext'}
              ></textarea>
              <div class="small text-muted mt-1">
                {createScope === 'single_file'
                  ? 'Provide exactly one absolute file path.'
                  : 'Provide one absolute path per line. Duplicate lines are ignored.'}
              </div>
            </div>
          {/if}
        </div>

      </div>

    {:else if activeTab === TAB_HISTORY}
      <div class="panel-head">
        <h2>Checkpoint History</h2>
        <span class="panel-badge">{sessions.length} sessions</span>
      </div>
      <div class="panel-body p-0">
        {#if loadingSessions}
          <div class="empty-state">Loading sessions...</div>
        {:else if sessions.length === 0}
          <div class="empty-state">No checkpoints for this watched folder yet.</div>
        {:else}
          <div class="table-wrap">
            <table class="table mb-0 align-middle checkpoint-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Project</th>
                  <th>Scope</th>
                  <th class="col-items">Items</th>
                  <th class="col-created">Created</th>
                  <th class="text-end pe-3 col-actions">Actions</th>
                </tr>
              </thead>
              <tbody>
                {#each sessions as session (session.id)}
                  <tr>
                    <td class="fw-semibold">{formatCheckpointName(session.name)}</td>
                    <td>{projectNameFromPath(session.watched_path)}</td>
                    <td>{toScopeLabel(session.scope)}</td>
                    <td class="col-items">{session.item_count}</td>
                    <td class="small text-muted col-created">{formatTime(session.created_at)}</td>
                    <td class="text-end pe-3">
                      <div class="history-actions-row">
                        <button
                          class="btn btn-sm btn-secondary"
                          on:click={() => renameSession(session)}
                          disabled={renamingSessionId === String(session.id)}
                        >
                          {renamingSessionId === String(session.id) ? 'Saving...' : 'Rename'}
                        </button>
                      </div>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </div>

    {:else if activeTab === TAB_DIFF}
      <div class="panel-head">
        <h2>Diff Explorer</h2>
        <span class="panel-badge">Session Compare</span>
      </div>
      <div class="panel-body">
        <div class="diff-controls">
          <div class="diff-control diff-control-from">
            <label class="form-label fw-semibold" for="checkpoint-diff-from">From</label>
            <select id="checkpoint-diff-from" class="form-select" bind:value={fromSessionId}>
              <option value="">Select base session</option>
              {#each sessions as session (session.id)}
                <option value={String(session.id)}>{formatCheckpointName(session.name)} ({formatTime(session.created_at)})</option>
              {/each}
            </select>
          </div>

          <div class="diff-control diff-control-to">
            <label class="form-label fw-semibold" for="checkpoint-diff-to">To</label>
            <select id="checkpoint-diff-to" class="form-select" bind:value={toSessionId}>
              <option value="">Select target session</option>
              {#each sessions as session (session.id)}
                <option value={String(session.id)}>{formatCheckpointName(session.name)} ({formatTime(session.created_at)})</option>
              {/each}
            </select>
          </div>

          <div class="diff-control diff-controls-inline">
            <div class="form-check">
              <input id="include-unchanged" class="form-check-input" type="checkbox" bind:checked={includeUnchanged} />
              <label class="form-check-label" for="include-unchanged">Include unchanged</label>
            </div>
          </div>

          <div class="diff-control diff-controls-action">
            <button class="btn btn-primary" on:click={compareSessions} disabled={diffLoading || loadingSessions || sessions.length < 2}>
              {diffLoading ? 'Comparing...' : 'Compare'}
            </button>
          </div>
        </div>

        {#if diffResult}
          <div class="summary-badges mb-3">
            <span class="badge-soft badge-soft-success">Added: {diffResult.summary.added}</span>
            <span class="badge-soft badge-soft-danger">Removed: {diffResult.summary.removed}</span>
            <span class="badge-soft badge-soft-secondary">Modified: {diffResult.summary.modified}</span>
            <span class="badge-soft badge-soft-secondary">Renamed: {diffResult.summary.renamed}</span>
            <span class="badge-soft badge-soft-success">+{diffResult.summary.added_lines || 0} lines</span>
            <span class="badge-soft badge-soft-danger">-{diffResult.summary.removed_lines || 0} lines</span>
            {#if includeUnchanged}
              <span class="badge-soft badge-soft-secondary">Unchanged: {diffResult.summary.unchanged}</span>
            {/if}
          </div>

          <div class="diff-explorer {isDiffFilesPaneCollapsed ? 'is-files-collapsed' : ''}">
            <aside class="diff-files-pane">
              <div class="diff-pane-head">
                <div class="diff-pane-head-left">
                  <button
                    type="button"
                    class="pane-collapse-btn {isDiffFilesPaneCollapsed ? 'is-collapsed' : ''}"
                    on:click={() => (isDiffFilesPaneCollapsed = !isDiffFilesPaneCollapsed)}
                    aria-label={isDiffFilesPaneCollapsed ? 'Expand changed files pane' : 'Collapse changed files pane'}
                    title={isDiffFilesPaneCollapsed ? 'Expand changed files pane' : 'Collapse changed files pane'}
                  >
                    <span class="pane-collapse-caret" aria-hidden="true"></span>
                  </button>
                  <h3 class="diff-pane-title">Changed Files</h3>
                </div>
                <span class="small text-muted diff-pane-total">{totalChangedFiles} total</span>
              </div>

              <div class="diff-files-scroll">
                {#if modifiedItems.length === 0}
                  <div class="empty-state diff-empty">No modified files in this comparison.</div>
                {:else}
                  <ul class="diff-tree-list">
                    {#each diffTreeRows as row (row.key)}
                      <li>
                        {#if row.kind === 'folder'}
                          <button
                            class="diff-tree-row diff-folder-row"
                            type="button"
                            on:click={() => toggleDiffFolder(row.key)}
                            style={`padding-left: ${row.depth * 14 + 10}px`}
                          >
                            <span class="tree-caret {row.expanded ? 'is-expanded' : ''}" aria-hidden="true"></span>
                            <span class="tree-name">{row.name}</span>
                            <span class="tree-meta">{row.childCount}</span>
                          </button>
                        {:else}
                          <button
                            class="diff-tree-row diff-file-row {selectedDiffFilePath === filePathKey(row.item) ? 'is-active' : ''}"
                            type="button"
                            on:click={() => selectDiffFile(row.item)}
                            style={`padding-left: ${row.depth * 14 + 24}px`}
                          >
                            <span class="tree-name mono-text" title={row.fullPath}>{row.name}</span>
                            <span class="line-badges">
                              <span class="line-delta line-add">{formatDelta(row.item?.added_lines, '+')}</span>
                              <span class="line-delta line-remove">{formatDelta(row.item?.removed_lines, '-')}</span>
                            </span>
                          </button>
                        {/if}
                      </li>
                    {/each}
                  </ul>
                {/if}
              </div>
            </aside>

            <section class="diff-view-pane">
              {#if selectedDiffItem}
                <header class="file-diff-head diff-view-head">
                  <div class="mono-text file-path">{selectedDiffItem.file_path}</div>
                  <div class="line-badges">
                    <span class="line-delta line-add">{formatDelta(selectedDiffItem.added_lines, '+')}</span>
                    <span class="line-delta line-remove">{formatDelta(selectedDiffItem.removed_lines, '-')}</span>
                  </div>
                </header>

                {#if selectedDiffContentLoading}
                  <div class="empty-state diff-empty">Loading full file states...</div>
                {:else if selectedDiffContentError}
                  <div class="empty-state diff-empty">{selectedDiffContentError}</div>
                {:else}
                  <div class="file-state-grid">
                    <section class="file-state-column">
                      <div class="file-state-column-head">Before (From)</div>
                      {#if selectedDiffBeforeState?.type !== 'text'}
                        <div class="empty-state diff-empty">{selectedDiffBeforeState?.content || 'Before version is not text-renderable.'}</div>
                      {:else}
                        <div class="file-state-pane" bind:this={beforeStatePaneEl} on:scroll={onBeforeStateScroll}>
                          {#if selectedDiffBeforeLines.length === 0}
                            <div class="empty-state diff-empty">File was empty in the From session.</div>
                          {:else}
                            <div class="file-state-line-list mono-text">
                              {#each selectedDiffBeforeLines as line, idx (idx)}
                                <div class="state-line {selectedDiffBeforeHighlights.has(idx + 1) ? 'is-changed-removed' : ''}">
                                  <span class="state-line-number">{idx + 1}</span>
                                  <span class="state-line-content">{line || ' '}</span>
                                </div>
                              {/each}
                            </div>
                          {/if}
                        </div>
                      {/if}
                    </section>

                    <section class="file-state-column">
                      <div class="file-state-column-head">After (To)</div>
                      {#if selectedDiffAfterState?.type !== 'text'}
                        <div class="empty-state diff-empty">{selectedDiffAfterState?.content || 'After version is not text-renderable.'}</div>
                      {:else}
                        <div class="file-state-pane" bind:this={afterStatePaneEl} on:scroll={onAfterStateScroll}>
                          {#if selectedDiffAfterLines.length === 0}
                            <div class="empty-state diff-empty">File is empty in the To session.</div>
                          {:else}
                            <div class="file-state-line-list mono-text">
                              {#each selectedDiffAfterLines as line, idx (idx)}
                                <div class="state-line {selectedDiffAfterHighlights.has(idx + 1) ? 'is-changed-added' : ''}">
                                  <span class="state-line-number">{idx + 1}</span>
                                  <span class="state-line-content">{line || ' '}</span>
                                </div>
                              {/each}
                            </div>
                          {/if}
                        </div>
                      {/if}
                    </section>
                  </div>

                  <div class="small text-muted diff-footnote">
                    {#if selectedDiffItem.line_diff?.available}
                      Highlighted lines mark changed regions in each file state.
                    {:else}
                      {getDiffReason(selectedDiffItem.line_diff?.reason)}
                    {/if}
                  </div>
                {/if}
              {:else}
                <div class="empty-state diff-empty">Select a modified file to inspect line-level changes.</div>
              {/if}
            </section>
          </div>

          <details class="diff-secondary-block mt-1" open={modifiedItems.length === 0}>
            <summary class="diff-secondary-summary">
              <span>Other file lists</span>
              <span class="small text-muted">Added {diffResult.added.length}, Removed {diffResult.removed.length}, Renamed {diffResult.renamed.length}{#if includeUnchanged}, Unchanged {diffResult.unchanged.length}{/if}</span>
            </summary>

            <div class="diff-grid mt-2">
              <section class="diff-block">
                <h3>Added ({diffResult.added.length})</h3>
                {#if diffResult.added.length === 0}
                  <div class="small text-muted">No added files.</div>
                {:else}
                  <ul class="diff-list mono-text">
                    {#each diffResult.added as item (item.file_path)}
                      <li>{item.file_path}</li>
                    {/each}
                  </ul>
                {/if}
              </section>

              <section class="diff-block">
                <h3>Removed ({diffResult.removed.length})</h3>
                {#if diffResult.removed.length === 0}
                  <div class="small text-muted">No removed files.</div>
                {:else}
                  <ul class="diff-list mono-text">
                    {#each diffResult.removed as item (item.file_path)}
                      <li>{item.file_path}</li>
                    {/each}
                  </ul>
                {/if}
              </section>

              <section class="diff-block">
                <h3>Renamed ({diffResult.renamed.length})</h3>
                {#if diffResult.renamed.length === 0}
                  <div class="small text-muted">No renamed files.</div>
                {:else}
                  <ul class="diff-list mono-text">
                    {#each diffResult.renamed as item (`${item.from_path}->${item.to_path}`)}
                      <li>{item.from_path} -> {item.to_path}</li>
                    {/each}
                  </ul>
                {/if}
              </section>

              {#if includeUnchanged}
                <section class="diff-block">
                  <h3>Unchanged ({diffResult.unchanged.length})</h3>
                  {#if diffResult.unchanged.length === 0}
                    <div class="small text-muted">No unchanged files.</div>
                  {:else}
                    <ul class="diff-list mono-text">
                      {#each diffResult.unchanged as item (item.file_path)}
                        <li>{item.file_path}</li>
                      {/each}
                    </ul>
                  {/if}
                </section>
              {/if}
            </div>
          </details>
        {:else}
          <div class="empty-state">Select two sessions and compare to render file and line-level changes.</div>
        {/if}
      </div>

    {:else if activeTab === TAB_RESTORE}
      <div class="panel-head">
        <h2>Restore Session</h2>
        <span class="panel-badge">Preview Then Execute</span>
      </div>
      <div class="panel-body">
        {#if !selectedWatchedPath}
          <div class="empty-state">Select a tracked project first to preview or restore checkpoint files.</div>
        {:else}
          <div class="restore-controls-grid">
            <div>
              <label class="form-label fw-semibold" for="checkpoint-restore-session">Session</label>
              <select id="checkpoint-restore-session" class="form-select" bind:value={restoreSessionId}>
                <option value="">Select session</option>
                {#each sessions as session (session.id)}
                  <option value={String(session.id)}>{formatCheckpointName(session.name)} ({formatTime(session.created_at)})</option>
                {/each}
              </select>
            </div>

            <div>
              <label class="form-label fw-semibold" for="checkpoint-restore-destination">Destination Root (optional)</label>
              <input
                id="checkpoint-restore-destination"
                class="form-control mono-input"
                type="text"
                bind:value={restoreDestinationRoot}
                placeholder="Leave empty to restore inside original watched folder"
              />
            </div>

            <div>
              <label class="form-label fw-semibold" for="checkpoint-restore-strategy">Conflict Strategy</label>
              <select id="checkpoint-restore-strategy" class="form-select" bind:value={restoreConflictStrategy}>
                <option value="rename">Rename</option>
                <option value="overwrite">Overwrite</option>
                <option value="skip">Skip</option>
              </select>
            </div>
          </div>

          {#if restorePreview}
            <div class="restore-preview">
              <div class="summary-badges mb-2">
                <span class="badge-soft badge-soft-secondary">Planned: {restorePreview.summary.planned}</span>
                {#if restorePreview.dry_run}
                  <span class="badge-soft badge-soft-success">Would Restore: {restorePreview.summary.would_restore}</span>
                {:else}
                  <span class="badge-soft badge-soft-success">Restored: {restorePreview.summary.restored}</span>
                  <span class="badge-soft badge-soft-danger">Failed: {restorePreview.summary.failed}</span>
                {/if}
                <span class="badge-soft badge-soft-secondary">Conflicts: {restorePreview.summary.conflicts}</span>
                <span class="badge-soft badge-soft-secondary">Skipped: {restorePreview.summary.skipped}</span>
              </div>

              {#if restorePreview.conflicts?.length > 0}
                <details class="diff-block" open>
                  <summary>Conflicts ({restorePreview.conflicts.length})</summary>
                  <ul class="diff-list mono-text">
                    {#each restorePreview.conflicts as conflict, idx (`${conflict.file_path}|${conflict.resolved_target_path}|${conflict.action}|${idx}`)}
                      <li>{conflict.file_path} -> {conflict.resolved_target_path} ({conflict.action})</li>
                    {/each}
                  </ul>
                </details>
              {/if}

              {#if restorePreview.failed?.length > 0}
                <details class="diff-block mt-2" open>
                  <summary>Failed ({restorePreview.failed.length})</summary>
                  <ul class="diff-list mono-text">
                    {#each restorePreview.failed as row, idx (`${row.file_path}|${row.reason}|${idx}`)}
                      <li>{row.file_path}: {row.reason}</li>
                    {/each}
                  </ul>
                </details>
              {/if}
            </div>
          {/if}

          <div class="panel-actions restore-actions-row">
            <button class="btn btn-outline-secondary" on:click={previewRestore} disabled={restoreLoading || restoreExecuting || sessions.length === 0}>
              {restoreLoading ? 'Previewing...' : 'Preview'}
            </button>
            <button class="btn btn-primary" on:click={executeRestore} disabled={restoreExecuting || restoreLoading || sessions.length === 0}>
              {restoreExecuting ? 'Restoring...' : 'Restore'}
            </button>
          </div>
        {/if}
      </div>
    {/if}
  </section>
</section>

<style>
  .checkpoint-page {
    display: flex;
    flex-direction: column;
    gap: 0.8rem;
    height: 100%;
    min-height: 0;
  }

  .checkpoint-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    padding: 0.2rem 0.1rem;
  }

  .checkpoint-header-actions {
    display: flex;
    align-items: flex-end;
    gap: 0.75rem;
    flex-wrap: wrap;
  }

  .project-picker {
    min-width: 220px;
  }

  .project-picker .form-label {
    margin-bottom: 0.25rem;
  }

  .checkpoint-header-actions .btn {
    width: auto;
    min-width: 96px;
    align-self: flex-start;
  }

  .refresh-header-btn {
    padding: 0.44rem 0.9rem;
    border-radius: 999px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-primary);
    font-weight: 650;
    box-shadow: none;
  }

  .refresh-header-btn:hover:not(:disabled) {
    background: var(--surface-soft);
    border-color: var(--border-strong);
    color: var(--text-primary);
  }

  .refresh-header-btn:disabled {
    opacity: 0.7;
  }

  .checkpoint-header h1 {
    margin: 0;
    font-size: 1.45rem;
    letter-spacing: -0.01em;
  }

  .checkpoint-top-nav {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    border-bottom: 1px solid var(--border-subtle);
    background: transparent;
    padding: 0 0.1rem;
    overflow-x: auto;
  }

  .top-nav-item {
    border: 1px solid transparent;
    border-bottom: none;
    background: transparent;
    color: var(--text-muted);
    border-radius: 0.6rem 0.6rem 0 0;
    padding: 0.46rem 0.82rem;
    font-weight: 600;
    font-size: 0.84rem;
    letter-spacing: 0.01em;
    text-transform: none;
    white-space: nowrap;
    transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
  }

  .top-nav-item:hover {
    background: var(--surface-soft);
    color: var(--text-primary);
    border-color: var(--border-strong);
  }

  .top-nav-item.is-active {
    color: var(--text-primary);
    background: var(--surface-elevated);
    border-color: var(--border-subtle);
    box-shadow: none;
  }

  .checkpoint-panel {
    flex: 1;
    min-height: 0;
    min-width: 0;
    border: 1px solid var(--border-subtle);
    border-radius: 0.75rem;
    background: var(--surface-elevated);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    box-shadow: var(--shadow-sm);
  }

  .panel-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.8rem 0.95rem;
    border-bottom: 1px solid var(--border-subtle);
    background: var(--surface-soft);
  }

  .panel-head h2 {
    margin: 0;
    font-size: 0.98rem;
    letter-spacing: 0.01em;
    text-transform: none;
    color: var(--text-primary);
  }

  .panel-badge {
    border: 1px solid var(--border-subtle);
    border-radius: 999px;
    background: var(--surface-elevated);
    color: var(--text-muted);
    font-size: 0.74rem;
    font-weight: 600;
    text-transform: none;
    letter-spacing: 0.01em;
    padding: 0.2rem 0.6rem;
  }

  .panel-body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 1rem;
  }

  .form-grid {
    display: flex;
    flex-direction: column;
    gap: 0.7rem;
    max-width: none;
    width: 100%;
  }

  .split-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 0.7rem;
  }

  .split-row-create {
    grid-template-columns: minmax(150px, 220px) minmax(220px, 1fr) auto;
    align-items: end;
  }

  .scope-field {
    max-width: 220px;
  }

  .scope-field .form-select {
    min-width: 0;
  }

  .create-action-cell {
    display: flex;
    justify-content: flex-end;
    align-items: end;
  }

  .create-action-cell .btn {
    min-width: 188px;
  }

  .panel-actions {
    margin-top: 0.3rem;
    display: flex;
    justify-content: flex-end;
    gap: 0.45rem;
    flex-wrap: wrap;
  }

  .table-wrap {
    max-height: min(72vh, calc(100vh - 270px));
    overflow: auto;
  }

  .checkpoint-table {
    table-layout: fixed;
    --bs-table-bg: var(--surface-elevated);
    --bs-table-color: var(--text-primary);
    --bs-table-border-color: var(--border-subtle);
    --bs-table-hover-bg: var(--surface-soft);
    --bs-table-striped-bg: var(--surface-soft);
  }

  .checkpoint-table th {
    position: sticky;
    top: 0;
    z-index: 2;
    background: var(--surface-soft);
    font-size: 0.73rem;
    text-transform: none;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border-subtle);
  }

  .checkpoint-table td {
    background: var(--surface-elevated);
    color: var(--text-primary);
    border-color: var(--border-subtle);
  }

  .checkpoint-table th,
  .checkpoint-table td {
    padding: 0.52rem 0.6rem;
  }

  .checkpoint-table .col-items {
    width: 70px;
  }

  .checkpoint-table .col-created {
    width: 190px;
  }

  .checkpoint-table .col-actions {
    width: 112px;
  }

  .history-actions-row {
    display: inline-flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0.4rem;
    flex-wrap: nowrap;
  }

  .checkpoint-table tbody tr:hover td {
    background: var(--surface-soft);
  }

  .diff-controls {
    display: grid;
    grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) auto auto;
    gap: 0.65rem;
    align-items: end;
    margin-bottom: 0.85rem;
  }

  .diff-control {
    min-width: 0;
  }

  .diff-controls-inline {
    display: inline-flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0.7rem;
    flex-wrap: nowrap;
    padding-bottom: 0.1rem;
    min-height: 40px;
  }

  .diff-controls-inline .form-check {
    margin: 0;
    white-space: nowrap;
  }

  .diff-controls-action {
    display: flex;
    align-items: end;
    justify-content: flex-end;
  }

  .diff-controls-action .btn {
    min-width: 120px;
  }

  .summary-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }

  .diff-explorer {
    --files-pane-width: clamp(180px, 24vw, 230px);
    display: grid;
    grid-template-columns: var(--files-pane-width) minmax(0, 1fr);
    gap: 0.75rem;
    min-height: min(72vh, calc(100vh - 280px));
    margin-bottom: 0.65rem;
    transition: grid-template-columns 0.24s cubic-bezier(0.22, 1, 0.36, 1);
  }

  .diff-explorer.is-files-collapsed {
    --files-pane-width: 44px;
  }

  .diff-files-pane,
  .diff-view-pane {
    border: 1px solid var(--border-subtle);
    border-radius: 0.7rem;
    background: var(--surface-elevated);
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .diff-files-pane {
    width: var(--files-pane-width);
    min-width: 0;
  }

  .diff-pane-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.55rem 0.7rem;
    border-bottom: 1px solid var(--border-subtle);
    background: color-mix(in srgb, var(--surface-soft) 88%, var(--surface-elevated));
  }

  .diff-pane-head-left {
    display: inline-flex;
    align-items: center;
    gap: 0.42rem;
    min-width: 0;
  }

  .diff-pane-title,
  .diff-pane-total {
    transition: opacity 0.2s ease, transform 0.24s ease, max-width 0.24s ease;
  }

  .pane-collapse-btn {
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-muted);
    border-radius: 999px;
    width: 1.3rem;
    height: 1.3rem;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    line-height: 1;
    cursor: pointer;
    transition: border-color 0.18s ease, color 0.18s ease, background-color 0.18s ease;
  }

  .pane-collapse-btn:hover {
    color: var(--text-primary);
    border-color: var(--border-strong);
  }

  .pane-collapse-caret {
    width: 0.4rem;
    height: 0.4rem;
    border-right: 2px solid currentColor;
    border-bottom: 2px solid currentColor;
    transform: rotate(135deg);
    transform-origin: 50% 50%;
    transition: transform 0.24s cubic-bezier(0.22, 1, 0.36, 1);
  }

  .pane-collapse-btn.is-collapsed .pane-collapse-caret {
    transform: rotate(-45deg);
  }

  .diff-explorer.is-files-collapsed .diff-pane-head {
    justify-content: center;
    padding-left: 0.35rem;
    padding-right: 0.35rem;
  }

  .diff-explorer.is-files-collapsed .diff-pane-head-left {
    width: 100%;
    justify-content: center;
  }

  .diff-explorer.is-files-collapsed .diff-pane-title,
  .diff-explorer.is-files-collapsed .diff-pane-total {
    opacity: 0;
    transform: translateX(-6px);
    max-width: 0;
    overflow: hidden;
    white-space: nowrap;
    pointer-events: none;
  }

  .diff-explorer.is-files-collapsed .diff-files-scroll {
    opacity: 0;
    visibility: hidden;
    pointer-events: none;
    transform: translateX(-6px);
  }

  .diff-pane-head h3 {
    margin: 0;
    font-size: 0.83rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    text-transform: none;
    color: var(--text-muted);
  }

  .diff-files-scroll {
    min-height: 0;
    overflow: auto;
    opacity: 1;
    visibility: visible;
    transform: translateX(0);
    transition: opacity 0.18s ease, visibility 0.18s ease, transform 0.24s ease;
  }

  .diff-tree-list {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .diff-tree-row {
    width: 100%;
    border: none;
    border-top: 1px solid var(--border-subtle);
    background: transparent;
    color: inherit;
    text-align: left;
    padding-top: 0.46rem;
    padding-bottom: 0.46rem;
    padding-right: 0.45rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
    transition: background-color 0.14s ease, box-shadow 0.14s ease;
  }

  .diff-tree-list > li:first-child > .diff-tree-row {
    border-top: none;
  }

  .diff-tree-row:hover {
    background: var(--surface-soft);
  }

  .diff-folder-row {
    color: var(--text-muted);
    font-size: 0.77rem;
    font-weight: 600;
  }

  .tree-caret {
    width: 0.52rem;
    height: 0.52rem;
    border-right: 2px solid currentColor;
    border-bottom: 2px solid currentColor;
    transform: rotate(-45deg);
    transform-origin: 50% 50%;
    transition: transform 0.18s ease;
    color: var(--text-muted);
    opacity: 0.9;
    flex: 0 0 auto;
  }

  .tree-caret.is-expanded {
    transform: rotate(45deg);
  }

  .tree-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }

  .tree-meta {
    font-size: 0.7rem;
    color: var(--text-muted);
    opacity: 0.8;
  }

  .diff-file-row {
    font-size: 0.76rem;
  }

  .diff-file-row.is-active {
    background: color-mix(in srgb, var(--accent-soft) 62%, var(--surface-elevated));
    box-shadow: inset 3px 0 0 var(--accent);
  }

  .diff-view-head {
    padding: 0.58rem 0.7rem;
    border-bottom: 1px solid var(--border-subtle);
    background: color-mix(in srgb, var(--surface-soft) 88%, var(--surface-elevated));
  }

  .file-state-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 0.55rem;
    padding: 0.6rem;
  }

  .file-state-column {
    border: 1px solid var(--border-subtle);
    border-radius: 0.55rem;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    min-height: 0;
    background: var(--surface-soft);
  }

  .file-state-column-head {
    border-bottom: 1px solid var(--border-subtle);
    padding: 0.34rem 0.55rem;
    font-size: 0.74rem;
    letter-spacing: 0.02em;
    color: var(--text-muted);
    font-weight: 700;
    background: color-mix(in srgb, var(--surface-elevated) 82%, var(--surface-soft));
  }

  .file-state-pane {
    min-height: 0;
    max-height: min(65vh, 650px);
    overflow: auto;
  }

  .file-state-line-list {
    font-size: 0.75rem;
  }

  .state-line {
    display: grid;
    grid-template-columns: 40px minmax(0, 1fr);
    align-items: start;
    gap: 0.28rem;
    padding: 0.14rem 0.34rem 0.14rem 0.18rem;
    border-bottom: 1px solid color-mix(in srgb, var(--border-subtle) 90%, transparent);
    line-height: 1.45;
  }

  .state-line:last-child {
    border-bottom: none;
  }

  .state-line-number {
    color: var(--text-muted);
    text-align: center;
    font-variant-numeric: tabular-nums;
    border-right: 1px solid color-mix(in srgb, var(--border-subtle) 88%, transparent);
    padding-right: 0.24rem;
    user-select: none;
  }

  .state-line-content {
    min-width: 0;
    white-space: pre;
  }

  .state-line.is-changed-added {
    background: rgba(46, 160, 67, 0.12);
  }

  .state-line.is-changed-removed {
    background: rgba(207, 34, 46, 0.1);
  }

  .diff-empty {
    padding: 0.9rem;
  }

  .diff-footnote {
    padding: 0.06rem 0.15rem 0.22rem;
  }

  .diff-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.65rem;
  }

  .diff-secondary-block {
    border: 1px solid var(--border-subtle);
    border-radius: 0.65rem;
    background: var(--surface-elevated);
    padding: 0.55rem 0.65rem;
  }

  .diff-secondary-summary {
    list-style: none;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.8rem;
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    cursor: pointer;
    color: var(--text-primary);
    padding: 0.12rem 0.18rem;
    border-radius: 0.45rem;
    transition: background-color 0.15s ease, box-shadow 0.15s ease;
  }

  .diff-secondary-summary:hover {
    background: var(--surface-soft);
  }

  .diff-secondary-summary::-webkit-details-marker {
    display: none;
  }

  .diff-secondary-summary::marker {
    content: '';
  }

  .diff-secondary-summary::before {
    content: '';
    width: 0.45rem;
    height: 0.45rem;
    border-right: 2px solid var(--text-muted);
    border-bottom: 2px solid var(--text-muted);
    margin-right: 0.35rem;
    transform: rotate(-45deg);
    transition: transform 0.16s ease;
    flex: 0 0 auto;
  }

  .diff-secondary-block[open] .diff-secondary-summary::before {
    transform: rotate(45deg);
  }

  .diff-block {
    border: 1px solid var(--border-subtle);
    border-radius: 0.65rem;
    background: var(--surface-soft);
    padding: 0.65rem 0.75rem;
    min-height: 0;
    overflow: hidden;
  }

  .diff-block h3 {
    margin: 0 0 0.45rem;
    font-size: 0.87rem;
    font-weight: 600;
    text-transform: none;
    letter-spacing: 0.01em;
    color: var(--text-primary);
  }

  .diff-list {
    margin: 0;
    padding-left: 1rem;
    max-height: 160px;
    overflow: auto;
    font-size: 0.8rem;
  }

  .file-diff-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.55rem;
  }

  .file-path {
    font-size: 0.78rem;
    color: var(--text-primary);
    word-break: break-all;
  }

  .line-badges {
    display: inline-flex;
    gap: 0.35rem;
    align-items: center;
  }

  .line-delta {
    font-size: 0.72rem;
    font-weight: 700;
    border-radius: 999px;
    padding: 0.12rem 0.45rem;
    border: 1px solid transparent;
  }

  .line-add {
    color: #1a7f37;
    background: rgba(46, 160, 67, 0.14);
    border-color: rgba(46, 160, 67, 0.28);
  }

  .line-remove {
    color: #cf222e;
    background: rgba(207, 34, 46, 0.12);
    border-color: rgba(207, 34, 46, 0.24);
  }

  .restore-preview {
    margin-top: 0.75rem;
    margin-bottom: 0.45rem;
  }

  .restore-controls-grid {
    display: grid;
    grid-template-columns: minmax(220px, 1fr) minmax(320px, 1.8fr) minmax(200px, 1fr);
    gap: 0.7rem;
    align-items: end;
  }

  .restore-actions-row {
    margin-top: 0.5rem;
  }

  .mono-input,
  .mono-text {
    font-family: var(--font-mono);
  }

  .empty-state {
    color: var(--text-muted);
    font-size: 0.86rem;
    padding: 0.8rem;
  }

  :global(body.theme-dark) .line-add {
    color: #3fb950;
    background: rgba(63, 185, 80, 0.18);
    border-color: rgba(63, 185, 80, 0.34);
  }

  :global(body.theme-dark) .line-remove {
    color: #f85149;
    background: rgba(248, 81, 73, 0.16);
    border-color: rgba(248, 81, 73, 0.3);
  }

  :global(body.theme-dark) .state-line.is-changed-added {
    background: rgba(63, 185, 80, 0.2);
  }

  :global(body.theme-dark) .state-line.is-changed-removed {
    background: rgba(248, 81, 73, 0.17);
  }

  @media (max-width: 1080px) {
    .diff-controls {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .diff-controls-inline {
      justify-content: flex-start;
      grid-column: 1 / -1;
    }

    .diff-controls-action {
      grid-column: 1 / -1;
      justify-content: flex-start;
    }

    .diff-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .diff-explorer {
      grid-template-columns: minmax(0, 1fr);
      min-height: 0;
    }

    .diff-files-pane {
      max-height: min(38vh, 320px);
    }

    .file-state-grid {
      grid-template-columns: minmax(0, 1fr);
    }

    .file-state-pane {
      max-height: min(36vh, 300px);
    }

    .diff-secondary-summary {
      flex-direction: column;
      align-items: flex-start;
    }

    .split-row {
      grid-template-columns: minmax(0, 1fr);
    }

    .create-action-cell {
      justify-content: flex-start;
    }

    .restore-controls-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (max-width: 620px) {
    .checkpoint-header {
      flex-direction: column;
      align-items: stretch;
    }

    .diff-controls {
      grid-template-columns: minmax(0, 1fr);
    }

    .diff-grid {
      grid-template-columns: minmax(0, 1fr);
    }

    .diff-files-pane {
      max-height: 220px;
    }

    .top-nav-item {
      font-size: 0.74rem;
      padding: 0.34rem 0.64rem;
    }

    .restore-controls-grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
