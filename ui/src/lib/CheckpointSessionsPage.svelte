<script>
  import { onMount } from 'svelte';
  import {
    createCheckpointSession,
    diffCheckpointSessions,
    getCheckpointSessionDetail,
    getWatchedPaths,
    listCheckpointSessions,
    renameCheckpointSession,
    restoreCheckpointSession
  } from '../api.js';
  import { askQuestion, showMessage } from '../dialogStore.js';

  const TAB_CREATE = 'create';
  const TAB_HISTORY = 'history';
  const TAB_DIFF = 'diff';
  const TAB_RESTORE = 'restore';
  const TAB_MANIFEST = 'manifest';

  const topTabs = [
    { id: TAB_CREATE, label: 'Create' },
    { id: TAB_HISTORY, label: 'History' },
    { id: TAB_DIFF, label: 'Diff Explorer' },
    { id: TAB_RESTORE, label: 'Restore' },
    { id: TAB_MANIFEST, label: 'Manifest' }
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

  let detailLoading = false;
  let selectedSessionDetail = null;

  let fromSessionId = '';
  let toSessionId = '';
  let includeUnchanged = false;
  let diffLoading = false;
  let diffResult = null;

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
    if (!value) return '';
    let normalized = value;
    if (typeof value === 'string' && !value.endsWith('Z') && !value.includes('+') && !value.includes('-')) {
      normalized = value.replace(' ', 'T') + 'Z';
    }
    const date = new Date(normalized);
    return new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    }).format(date);
  };

  const toScopeLabel = (scope) => {
    if (scope === 'single_file') return 'Single File';
    if (scope === 'selected_files') return 'Selected Files';
    return 'Full Folder';
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
    selectedSessionDetail = null;

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
      await showMessage(`Checkpoint created: ${created.name}`, 'Checkpoint');
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
    const next = prompt('Rename checkpoint', session.name || '');
    if (next == null) return;

    const cleaned = String(next).trim();
    if (!cleaned) {
      pageError = 'Checkpoint name cannot be empty.';
      return;
    }

    pageError = '';
    try {
      await renameCheckpointSession(session.id, cleaned);
      await loadSessions();
      if (selectedSessionDetail?.id === session.id) {
        selectedSessionDetail = {
          ...selectedSessionDetail,
          name: ' '.concat(cleaned).trim().replace(/\s+/g, ' ')
        };
      }
    } catch (e) {
      pageError = e.message || 'Failed to rename checkpoint';
    }
  };

  const viewManifest = async (sessionId) => {
    detailLoading = true;
    pageError = '';
    try {
      selectedSessionDetail = await getCheckpointSessionDetail(sessionId);
    } catch (e) {
      pageError = e.message || 'Failed to load checkpoint detail';
      selectedSessionDetail = null;
    } finally {
      detailLoading = false;
    }
  };

  const openManifest = async (sessionId) => {
    await viewManifest(sessionId);
    activeTab = TAB_MANIFEST;
  };

  const compareSessions = async () => {
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
    selectedSessionDetail = null;
    diffResult = null;
    restorePreview = null;
    restoreDestinationRoot = '';
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
    <button class="btn btn-outline-secondary" on:click={refreshAll} disabled={loadingSessions || creating || diffLoading || restoreLoading || restoreExecuting}>
      Refresh
    </button>
  </header>

  <nav class="checkpoint-top-nav" aria-label="Checkpoint navigation">
    {#each topTabs as tab}
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
          <div>
            <label class="form-label fw-semibold" for="checkpoint-watched-path">Watched Folder</label>
            <select id="checkpoint-watched-path" class="form-select" bind:value={selectedWatchedPath} on:change={onWatchedChange}>
              <option value="">Select watched folder</option>
              {#each watchedPaths as row}
                <option value={row.path}>{row.path}</option>
              {/each}
            </select>
          </div>

          <div class="split-row">
            <div>
              <label class="form-label fw-semibold" for="checkpoint-scope">Scope</label>
              <select id="checkpoint-scope" class="form-select" bind:value={createScope}>
                <option value="full_folder">Full Folder</option>
                <option value="single_file">Single File</option>
                <option value="selected_files">Selected Files</option>
              </select>
            </div>
            <div>
              <label class="form-label fw-semibold" for="checkpoint-name">Label</label>
              <input id="checkpoint-name" class="form-control" type="text" bind:value={createName} maxlength="80" placeholder="before-upgrade" />
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

        <div class="panel-actions">
          <button class="btn btn-primary" on:click={createSession} disabled={creating || !selectedWatchedPath}>
            {creating ? 'Creating...' : 'Create Checkpoint'}
          </button>
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
                  <th>Scope</th>
                  <th>Items</th>
                  <th>Created</th>
                  <th class="text-end pe-3">Actions</th>
                </tr>
              </thead>
              <tbody>
                {#each sessions as session}
                  <tr>
                    <td class="fw-semibold">{session.name}</td>
                    <td>{toScopeLabel(session.scope)}</td>
                    <td>{session.item_count}</td>
                    <td class="small text-muted">{formatTime(session.created_at)}</td>
                    <td class="text-end pe-3">
                      <div class="d-inline-flex gap-2 flex-wrap justify-content-end">
                        <button class="btn btn-sm btn-outline-secondary" on:click={() => renameSession(session)}>Rename</button>
                        <button class="btn btn-sm btn-outline-primary" on:click={() => openManifest(session.id)}>Manifest</button>
                        <button
                          class="btn btn-sm btn-outline-primary"
                          on:click={() => {
                            fromSessionId = String(session.id);
                            activeTab = TAB_DIFF;
                          }}
                        >
                          Use In Diff
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
          <div>
            <label class="form-label fw-semibold" for="checkpoint-diff-from">From</label>
            <select id="checkpoint-diff-from" class="form-select" bind:value={fromSessionId}>
              <option value="">Select base session</option>
              {#each sessions as session}
                <option value={String(session.id)}>{session.name} ({formatTime(session.created_at)})</option>
              {/each}
            </select>
          </div>

          <div>
            <label class="form-label fw-semibold" for="checkpoint-diff-to">To</label>
            <select id="checkpoint-diff-to" class="form-select" bind:value={toSessionId}>
              <option value="">Select target session</option>
              {#each sessions as session}
                <option value={String(session.id)}>{session.name} ({formatTime(session.created_at)})</option>
              {/each}
            </select>
          </div>

          <div class="diff-controls-inline">
            <div class="form-check">
              <input id="include-unchanged" class="form-check-input" type="checkbox" bind:checked={includeUnchanged} />
              <label class="form-check-label" for="include-unchanged">Include unchanged</label>
            </div>
            <button class="btn btn-primary" on:click={compareSessions} disabled={diffLoading || sessions.length < 2}>
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

          <section class="diff-block modified-block diff-primary-block">
            <h3>Modified ({diffResult.modified.length})</h3>
            {#if diffResult.modified.length === 0}
              <div class="small text-muted">No modified files.</div>
            {:else}
              <div class="modified-list">
                {#each diffResult.modified as item}
                  <article class="file-diff-card">
                    <header class="file-diff-head">
                      <div class="mono-text file-path">{item.file_path}</div>
                      <div class="line-badges">
                        <span class="line-delta line-add">{formatDelta(item.added_lines, '+')}</span>
                        <span class="line-delta line-remove">{formatDelta(item.removed_lines, '-')}</span>
                      </div>
                    </header>

                    {#if item.line_diff?.available}
                      <div class="hunk-list">
                        {#each item.line_diff.hunks as hunk}
                          <div class="hunk">
                            <div class="hunk-head mono-text">@@ -{hunk.from_start},{hunk.from_count} +{hunk.to_start},{hunk.to_count} @@</div>
                            {#each hunk.removed_preview as line, removedIndex}
                              <div class="diff-line line-remove"><span class="line-number">{hunk.from_start + removedIndex}</span><span class="line-prefix">-</span><span class="line-content">{line || ' '}</span></div>
                            {/each}
                            {#each hunk.added_preview as line, addedIndex}
                              <div class="diff-line line-add"><span class="line-number">{hunk.to_start + addedIndex}</span><span class="line-prefix">+</span><span class="line-content">{line || ' '}</span></div>
                            {/each}
                          </div>
                        {/each}
                        {#if item.line_diff.truncated_hunks}
                          <div class="small text-muted">Additional hunks omitted for readability.</div>
                        {/if}
                      </div>
                    {:else}
                      <div class="small text-muted">{getDiffReason(item.line_diff?.reason)}</div>
                    {/if}
                  </article>
                {/each}
              </div>
            {/if}
          </section>

          <details class="diff-secondary-block mt-3" open={diffResult.modified.length === 0}>
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
                    {#each diffResult.added as item}
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
                    {#each diffResult.removed as item}
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
                    {#each diffResult.renamed as item}
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
                      {#each diffResult.unchanged as item}
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
        <div class="form-grid">
          <div>
            <label class="form-label fw-semibold" for="checkpoint-restore-session">Session</label>
            <select id="checkpoint-restore-session" class="form-select" bind:value={restoreSessionId}>
              <option value="">Select session</option>
              {#each sessions as session}
                <option value={String(session.id)}>{session.name} ({formatTime(session.created_at)})</option>
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

          <div class="panel-actions">
            <button class="btn btn-outline-secondary" on:click={previewRestore} disabled={restoreLoading || restoreExecuting || sessions.length === 0}>
              {restoreLoading ? 'Previewing...' : 'Preview'}
            </button>
            <button class="btn btn-primary" on:click={executeRestore} disabled={restoreExecuting || restoreLoading || sessions.length === 0}>
              {restoreExecuting ? 'Restoring...' : 'Restore'}
            </button>
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
                  {#each restorePreview.conflicts as conflict}
                    <li>{conflict.file_path} -> {conflict.resolved_target_path} ({conflict.action})</li>
                  {/each}
                </ul>
              </details>
            {/if}

            {#if restorePreview.failed?.length > 0}
              <details class="diff-block mt-2" open>
                <summary>Failed ({restorePreview.failed.length})</summary>
                <ul class="diff-list mono-text">
                  {#each restorePreview.failed as row}
                    <li>{row.file_path}: {row.reason}</li>
                  {/each}
                </ul>
              </details>
            {/if}
          </div>
        {/if}
      </div>

    {:else}
      <div class="panel-head">
        <h2>Checkpoint Manifest</h2>
        {#if selectedSessionDetail}
          <span class="panel-badge">Session #{selectedSessionDetail.id}</span>
        {/if}
      </div>
      <div class="panel-body">
        {#if detailLoading}
          <div class="empty-state">Loading manifest...</div>
        {:else if !selectedSessionDetail}
          <div class="empty-state">Open a checkpoint from History to inspect exact file versions.</div>
        {:else}
          <div class="manifest-meta">
            <div class="fw-semibold">{selectedSessionDetail.name}</div>
            <div class="small text-muted">{toScopeLabel(selectedSessionDetail.scope)} | {selectedSessionDetail.item_count} items | {formatTime(selectedSessionDetail.created_at)}</div>
          </div>

          <div class="manifest-list">
            {#each selectedSessionDetail.items as item}
              <div class="manifest-row">
                <span class="mono-text text-truncate">{item.file_path}</span>
                <span class="small text-muted">v{item.file_version_id}</span>
              </div>
            {/each}
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

  .checkpoint-header .btn {
    width: auto;
    min-width: 120px;
    align-self: flex-start;
  }

  .checkpoint-header h1 {
    margin: 0;
    font-size: 1.45rem;
    letter-spacing: -0.01em;
  }

  .checkpoint-top-nav {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    border: 1px solid var(--border-subtle);
    border-radius: 0.75rem;
    background: var(--surface-elevated);
    padding: 0.45rem;
    overflow-x: auto;
  }

  .top-nav-item {
    border: 1px solid var(--border-subtle);
    background: var(--surface-soft);
    color: var(--text-muted);
    border-radius: 999px;
    padding: 0.38rem 0.8rem;
    font-weight: 700;
    font-size: 0.8rem;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    white-space: nowrap;
    transition: all 0.15s ease;
  }

  .top-nav-item:hover {
    color: var(--text-primary);
    border-color: var(--border-strong);
  }

  .top-nav-item.is-active {
    color: #fff;
    background: var(--accent);
    border-color: var(--accent);
    box-shadow: 0 6px 16px var(--accent-soft);
  }

  .checkpoint-panel {
    flex: 1;
    min-height: 0;
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
    background: linear-gradient(180deg, var(--surface-soft), transparent);
  }

  .panel-head h2 {
    margin: 0;
    font-size: 0.96rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--text-muted);
  }

  .panel-badge {
    border: 1px solid var(--border-subtle);
    border-radius: 999px;
    background: var(--surface-soft);
    color: var(--text-muted);
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    padding: 0.2rem 0.6rem;
  }

  .panel-body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 0.95rem;
  }

  .form-grid {
    display: flex;
    flex-direction: column;
    gap: 0.7rem;
    max-width: 980px;
  }

  .split-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 0.7rem;
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
  }

  .checkpoint-table th {
    position: sticky;
    top: 0;
    z-index: 2;
    background: var(--surface-elevated);
    font-size: 0.73rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border-subtle);
  }

  .diff-controls {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(260px, 320px);
    gap: 0.65rem;
    align-items: end;
    margin-bottom: 0.85rem;
  }

  .diff-controls-inline {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.7rem;
    padding-bottom: 0.1rem;
  }

  .summary-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }

  .diff-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.65rem;
  }

  .diff-primary-block {
    border-color: color-mix(in srgb, var(--accent) 22%, var(--border-subtle));
    background: color-mix(in srgb, var(--surface-elevated) 92%, var(--accent-soft));
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

  .diff-secondary-summary::before {
    content: '▸';
    color: var(--text-muted);
    margin-right: 0.35rem;
    transition: transform 0.16s ease;
  }

  .diff-secondary-block[open] .diff-secondary-summary::before {
    transform: rotate(90deg);
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
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
  }

  .diff-list {
    margin: 0;
    padding-left: 1rem;
    max-height: 160px;
    overflow: auto;
    font-size: 0.8rem;
  }

  .modified-block {
    padding: 0.65rem;
  }

  .modified-list {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    max-height: min(62vh, calc(100vh - 345px));
    overflow: auto;
  }

  .file-diff-card {
    border: 1px solid var(--border-subtle);
    border-radius: 0.62rem;
    background: var(--surface-elevated);
    padding: 0.55rem;
    display: flex;
    flex-direction: column;
    gap: 0.45rem;
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

  .hunk-list {
    display: flex;
    flex-direction: column;
    gap: 0.45rem;
  }

  .hunk {
    border: 1px solid var(--border-subtle);
    border-radius: 0.5rem;
    overflow: hidden;
  }

  .hunk-head {
    padding: 0.2rem 0.5rem;
    font-size: 0.72rem;
    color: var(--text-muted);
    background: var(--surface-soft);
    border-bottom: 1px solid var(--border-subtle);
  }

  .diff-line {
    display: grid;
    grid-template-columns: 58px 14px minmax(0, 1fr);
    align-items: start;
    gap: 0.35rem;
    padding: 0.16rem 0.5rem;
    font-size: 0.74rem;
    font-family: var(--font-mono);
    white-space: pre-wrap;
    word-break: break-word;
  }

  .line-number {
    color: var(--text-muted);
    text-align: right;
    font-variant-numeric: tabular-nums;
    border-right: 1px solid color-mix(in srgb, var(--border-subtle) 88%, transparent);
    padding-right: 0.45rem;
    user-select: none;
  }

  .line-prefix {
    font-weight: 700;
    opacity: 0.9;
  }

  .line-content {
    min-width: 0;
  }

  .restore-preview {
    margin-top: 0.75rem;
  }

  .manifest-meta {
    margin-bottom: 0.55rem;
  }

  .manifest-list {
    border: 1px solid var(--border-subtle);
    border-radius: 0.65rem;
    max-height: min(70vh, calc(100vh - 300px));
    overflow: auto;
  }

  .manifest-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 0.75rem;
    padding: 0.45rem 0.65rem;
    border-bottom: 1px solid var(--border-subtle);
  }

  .manifest-row:last-child {
    border-bottom: none;
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

  @media (max-width: 1080px) {
    .diff-controls {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .diff-controls-inline {
      grid-column: 1 / -1;
      justify-content: flex-start;
      flex-wrap: wrap;
    }

    .diff-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .diff-secondary-summary {
      flex-direction: column;
      align-items: flex-start;
    }

    .split-row {
      grid-template-columns: minmax(0, 1fr);
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

    .modified-list {
      max-height: min(64vh, calc(100vh - 300px));
    }

    .top-nav-item {
      font-size: 0.74rem;
      padding: 0.34rem 0.64rem;
    }
  }
</style>