<script>
    import { onDestroy, onMount } from 'svelte';
    import { fade } from 'svelte/transition';
                import { getFileVersions, restoreFileVersion, getFileVersionContent, getCurrentFileVersion, getCurrentFileContent } from '../api.js';
    import { showMessage, askQuestion } from '../dialogStore.js';
    import Fa from 'svelte-fa';
    import { faArrowLeft, faHistory, faUndo, faEye } from '@fortawesome/free-solid-svg-icons';

  export let filePath = null;
  export let onClose = () => {};

    const setModalOpenState = (open) => {
        if (typeof document === 'undefined') return;
        document.body.classList.toggle('locus-modal-open', !!open);
    };

  let versions = [];
  let loading = false;
  let error = null;
  let successMsg = null;

    // Current state (what the on-disk file currently matches)
    let currentVersionNumber = null;
    let currentVersionId = null;
    let currentHash = null;
  
  // Preview State
  let selectedVersion = null;
  let viewMode = 'preview';
  let previewContent = null;
  let previewLoading = false;
  let previewType = 'text';
    let currentContent = null;
    let currentContentType = 'text';
    let diffRows = [];
    let diffTooLarge = false;
        let previewLines = [];

    const MAX_DIFF_TOTAL_LINES = 1600;

  // React to filePath changes
  $: if (filePath) {
      setModalOpenState(true);
      loadVersions();
      closePreview();
  } else {
      setModalOpenState(false);
  }

  onDestroy(() => {
      setModalOpenState(false);
  });

  function closePreview() {
      selectedVersion = null;
      previewContent = null;
      previewLoading = false;
      currentContent = null;
      currentContentType = 'text';
      diffRows = [];
      diffTooLarge = false;
      previewLines = [];
      viewMode = 'preview';
  }

  function splitLines(text) {
      return String(text ?? '').replace(/\r\n/g, '\n').split('\n');
  }

  function buildLineDiff(currentText, targetText) {
      const currentLines = splitLines(currentText);
      const targetLines = splitLines(targetText);

      if (currentLines.length + targetLines.length > MAX_DIFF_TOTAL_LINES) {
          return null;
      }

      const m = currentLines.length;
      const n = targetLines.length;
      const dp = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));

      for (let i = 1; i <= m; i += 1) {
          for (let j = 1; j <= n; j += 1) {
              if (currentLines[i - 1] === targetLines[j - 1]) {
                  dp[i][j] = dp[i - 1][j - 1] + 1;
              } else {
                  dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
              }
          }
      }

      const ops = [];
      let i = m;
      let j = n;

      while (i > 0 && j > 0) {
          if (currentLines[i - 1] === targetLines[j - 1]) {
              ops.push({ type: 'context', text: currentLines[i - 1] });
              i -= 1;
              j -= 1;
          } else if (dp[i - 1][j] >= dp[i][j - 1]) {
              ops.push({ type: 'remove', text: currentLines[i - 1] });
              i -= 1;
          } else {
              ops.push({ type: 'add', text: targetLines[j - 1] });
              j -= 1;
          }
      }

      while (i > 0) {
          ops.push({ type: 'remove', text: currentLines[i - 1] });
          i -= 1;
      }

      while (j > 0) {
          ops.push({ type: 'add', text: targetLines[j - 1] });
          j -= 1;
      }

      const ordered = ops.reverse();
      let oldLine = 1;
      let newLine = 1;
      return ordered.map((row) => {
          if (row.type === 'context') {
              const normalized = { ...row, oldNo: oldLine, newNo: newLine, sign: ' ' };
              oldLine += 1;
              newLine += 1;
              return normalized;
          }
          if (row.type === 'remove') {
              const normalized = { ...row, oldNo: oldLine, newNo: null, sign: '-' };
              oldLine += 1;
              return normalized;
          }
          const normalized = { ...row, oldNo: null, newNo: newLine, sign: '+' };
          newLine += 1;
          return normalized;
      });
  }

  async function openPreview(version) {
      selectedVersion = version;
      previewLoading = true;
      error = null;
      currentContent = null;
      currentContentType = 'text';
      diffRows = [];
      diffTooLarge = false;
      viewMode = 'preview';
      try {
          const data = await getFileVersionContent(version.id);
          previewContent = data.content;
          previewType = data.type;
          previewLines = splitLines(previewContent ?? '');

          if (currentVersionId !== version.id && previewType === 'text') {
              try {
                  const currentData = await getCurrentFileContent(filePath);
                  currentContent = currentData.content;
                  currentContentType = currentData.type;
              } catch (compareErr) {
                  console.warn('Diff compare unavailable:', compareErr);
                  currentContent = null;
                  currentContentType = 'unavailable';
              }
          }

          if (
              currentVersionId !== version.id &&
              previewType === 'text' &&
              currentContentType === 'text'
          ) {
              const rows = buildLineDiff(previewContent, currentContent);
              if (rows === null) {
                  diffTooLarge = true;
              } else {
                  diffRows = rows;
              }
          }
      } catch (e) {
          error = "Failed to load preview: " + e.message;
          selectedVersion = null; // Exit preview on error
      } finally {
          previewLoading = false;
      }
  }

    $: canShowDiffToggle = Boolean(
      selectedVersion &&
      currentVersionId !== selectedVersion.id &&
      previewType === 'text' &&
      currentContentType === 'text' &&
      !diffTooLarge
  );

    $: showDiff = Boolean(canShowDiffToggle);

    $: diffAddedCount = diffRows.filter((row) => row.type === 'add').length;
    $: diffRemovedCount = diffRows.filter((row) => row.type === 'remove').length;
    $: if (!selectedVersion) {
        previewLines = [];
    }

  async function loadVersions() {
    if (!filePath) return;
    loading = true;
    error = null;
    successMsg = null;
    try {
      versions = await getFileVersions(filePath);
            const current = await getCurrentFileVersion(filePath);
            currentVersionNumber = current.version_number;
            currentVersionId = current.version_id;
            currentHash = current.file_hash;
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  async function handleRestore(versionId) {
    let shouldRestore = await askQuestion(
      "Are you sure you want to restore this version? This will overwrite the current file.",
      'Restore File',
      { type: 'warning', okLabel: 'Restore Now', cancelLabel: 'Cancel' }
    );

    if(!shouldRestore) return;
    
    try {
      loading = true; // Show loading indicator during restore
      const resp = await restoreFileVersion(versionId);
      successMsg = `File restored to V${resp.version} successfully!`;
      showMessage(successMsg, 'Success');
      // Close preview if open
      closePreview();
      // Refresh list
      await loadVersions();
      setTimeout(() => successMsg = null, 4000);
    } catch (e) {
      error = "Restore failed: " + e.message;
      showMessage(error, 'Error', 'error');
    } finally {
      loading = false;
    }
  }
  
  function formatSize(bytes) {
      if (bytes === 0) return '0 Bytes';
      const k = 1024;
      const sizes = ['Bytes', 'KB', 'MB', 'GB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  }

  function formatDate(dateString) {
      if (!dateString) return 'Unknown date';
      // Ensure dateString is treated as UTC
      const date = new Date(dateString + (dateString.includes('Z') ? '' : 'Z'));
            return new Intl.DateTimeFormat(undefined, {
                weekday: 'short',
                day: '2-digit',
                month: 'short',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: true
            }).format(date);
  }
</script>

{#if filePath}
<div transition:fade={{ duration: 200 }}>
<!-- Modal Backdrop -->
<div class="modal-backdrop show" role="button" on:click|self={onClose} on:keydown={(e) => e.key === 'Escape' && onClose()} tabindex="-1"></div>

<!-- Modal Dialog -->
<div class="modal show" tabindex="-1" role="dialog" aria-modal="true" on:click|self={onClose} on:keydown={(e) => e.key === 'Escape' && onClose()}>
        <div class="modal-dialog modal-xl" role="document">
    <div class="modal-content shadow-lg glass-modal">
            <div class="modal-header modal-header-shell {selectedVersion ? 'preview-header' : 'history-header'}">
                <div class="header-left">
                    {#if selectedVersion}
                        <button class="btn btn-sm btn-outline-secondary me-2" on:click={closePreview}>
                            <Fa icon={faArrowLeft} class="me-1" aria-hidden="true"/> Back
                        </button>
                        <h5 class="modal-title mb-0">File Preview (V{selectedVersion.version_number})</h5>
                    {:else}
                        <h5 class="modal-title mb-0">
                            <Fa icon={faHistory} class="me-2" aria-hidden="true"/>File History
                            {#if currentVersionNumber}
                                <span class="ms-2 small text-muted">(Current: V{currentVersionNumber})</span>
                            {:else if currentHash}
                                <span class="ms-2 small text-muted">(Current: unsnapped changes)</span>
                            {/if}
                        </h5>
                    {/if}
                </div>
                <div class="header-right {selectedVersion ? 'with-controls' : 'close-only'}">
                    {#if selectedVersion}
                        <span class="recorded-chip">Recorded: {formatDate(selectedVersion.created_at)}</span>
                        {#if canShowDiffToggle}
                            <div class="btn-group btn-group-sm preview-mode-switch" role="group">
                                <input type="radio" class="btn-check" id="radioPreview" bind:group={viewMode} value="preview">
                                <label class="btn btn-outline-secondary" for="radioPreview">Preview</label>

                                <input type="radio" class="btn-check" id="radioDiff" bind:group={viewMode} value="diff">
                                <label class="btn btn-outline-secondary" for="radioDiff">Diff vs Current</label>
                            </div>
                        {/if}
                    {/if}
                    <button type="button" class="btn-close" aria-label="Close modal" on:click={onClose}></button>
                </div>
      </div>
      
      <div class="modal-body">
                <div class="file-meta-row mb-4">
                        <div class="file-meta-main">
                                <small class="text-uppercase fw-bold ls-1" style="font-size: 0.7rem; color: var(--accent); opacity: 0.8;">File Path</small>
                                <div class="text-break mt-1 fw-medium" style="color: var(--text-primary);">{filePath}</div>
                        </div>
                        {#if selectedVersion}
                                <button class="btn btn-primary btn-sm px-3" on:click={() => handleRestore(selectedVersion.id)}>
                                        <Fa icon={faUndo} class="me-1" aria-hidden="true"/> Restore
                                </button>
                        {/if}
        </div>

        {#if successMsg}
            <div class="alert soft-bg-success border-0 px-4 py-3 mb-4 rounded-3 d-flex justify-content-between align-items-center" role="alert">
                <span>{successMsg}</span>
                <button type="button" class="btn-close ms-auto" aria-label="Dismiss" on:click={() => successMsg = null}></button>
            </div>
        {/if}
        
        {#if error}
            <div class="alert soft-bg-danger border-0 px-4 py-3 mb-4 rounded-3" role="alert">
                {error}
            </div>
        {/if}

        {#if selectedVersion}
            <!-- Preview View -->
            <div transition:fade={{ duration: 200 }}>
            <div class="preview-container">
                {#if previewLoading}
                    <div class="text-center py-5">
                        <div class="spinner-border text-primary" role="status"></div>
                    </div>
                {:else}
                    <div class="card border-0">
                        <div class="card-body p-0 position-relative">
                            {#if showDiff && viewMode === 'diff'}
                                <div class="diff-legend px-3 pt-3 pb-2 mb-2 small text-muted d-flex gap-3 align-items-center justify-content-between border-bottom">
                                    <div class="d-flex gap-3">
                                        <span title="Green lines are code that was added in the latest version (not present here)"><span class="diff-pill diff-pill-add"></span> Added in current file</span>
                                        <span title="Red lines exist in this selected version, but were removed structurally in the latest file"><span class="diff-pill diff-pill-remove"></span> Removed in current file</span>
                                    </div>
                                    <div class="d-flex gap-2 fw-medium">
                                        <span class="badge badge-soft-success px-2 py-1 rounded-pill">+{diffAddedCount} additions</span>
                                        <span class="badge badge-soft-danger px-2 py-1 rounded-pill">-{diffRemovedCount} deletions</span>
                                    </div>
                                </div>
                                <div class="preview-scroll preview-text diff-text">{#each diffRows as row}
<div class="diff-line diff-{row.type}">
    <span class="diff-gutter">{row.oldNo ?? ''}</span>
    <span class="diff-gutter">{row.newNo ?? ''}</span>
    <span class="diff-sign">{row.sign}</span>
    <span class="diff-code">{row.text}</span>
</div>{/each}</div>
                            {:else if diffTooLarge}
                                <div class="text-muted small px-3 pt-3">Diff is too large to render here. Showing raw historical content.</div>
                                <div class="preview-scroll preview-text preview-lines-scroll">
                                    <div class="preview-lines-wrap">
                                        {#each previewLines as line, lineIndex}
                                            <div class="preview-line">
                                                <span class="preview-line-no">{lineIndex + 1}</span>
                                                <span class="preview-line-code">{line || ' '}</span>
                                            </div>
                                        {/each}
                                    </div>
                                </div>
                            {:else}
                                <div class="preview-scroll preview-text preview-lines-scroll">
                                    <div class="preview-lines-wrap">
                                        {#each previewLines as line, lineIndex}
                                            <div class="preview-line">
                                                <span class="preview-line-no">{lineIndex + 1}</span>
                                                <span class="preview-line-code">{line || ' '}</span>
                                            </div>
                                        {/each}
                                    </div>
                                </div>
                            {/if}
                        </div>
                    </div>
                {/if}
            </div>
            </div>
        
        {:else}
            <!-- Version List View -->
            <div transition:fade={{ duration: 200 }}>
            {#if loading}
                <div class="d-flex justify-content-center py-5">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">Loading...</span>
                    </div>
                </div>
            {:else if versions.length === 0}
                <div class="text-center py-5 text-muted empty-state rounded-4">
                    <p class="mb-0 fw-medium">No version history found for this file.</p>
                    <small>Modifications are tracked automatically by LOCUS.</small>
                </div>
            {:else}
                <div class="version-list-scroll">
                    <div class="list-group list-group-flush pt-2 pb-2">
                        {#each versions as v}
                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                            <!-- svelte-ignore a11y-no-static-element-interactions -->
                            <div 
                                class="list-group-item list-group-item-action d-flex justify-content-between align-items-center cursor-pointer version-row py-3 mb-2 rounded-3 shadow-sm border"
                                on:click={() => openPreview(v)}
                                on:keydown={(e) => e.key === 'Enter' && openPreview(v)}
                                role="button"
                                tabindex="0"
                                title="Click to preview content"
                                style="transition: all 0.2s ease-in-out;"
                            >
                                <div>
                                    <div class="d-flex align-items-center mb-1">
                                        <span class="badge badge-soft-primary px-2 py-1 me-3 rounded-pill fw-bold">V{v.version_number}</span>
                                        <span class="fw-semibold">{formatDate(v.created_at)}</span>
                                        {#if currentVersionId === v.id}
                                            <span class="ms-3 badge badge-soft-success px-2 py-1 rounded-pill"><Fa icon={faEye} class="me-1 d-inline-block" aria-hidden="true"/> current</span>
                                        {/if}
                                    </div>
                                    <div class="text-muted small mt-2 d-flex align-items-center gap-3">
                                        <span class="badge bg-light text-dark fw-medium border">{formatSize(v.file_size_bytes)}</span>
                                        {#if v.file_hash}
                                        <span class="text-truncate d-inline-block align-bottom text-muted" style="max-width: 250px; opacity: 0.8;" title={v.file_hash}><Fa icon={faHistory} class="me-1 d-inline-block" /> {v.file_hash.substring(0, 12)}...</span>
                                        {/if}
                                    </div>
                                </div>
                                <button 
                                    class="btn btn-sm btn-outline-primary rounded-pill px-4 fw-medium btn-preview-hover" 
                                    on:click|stopPropagation={() => openPreview(v)}
                                >
                                    <Fa icon={faEye} class="me-2" aria-hidden="true"/>Preview
                                </button>
                            </div>
                        {/each}
                    </div>
                </div>
            {/if}
            </div>
        {/if}
      </div>
      
    </div>
  </div>
</div>
</div>
{/if}

<style>
    .modal-backdrop {
        z-index: 1040;
        position: fixed;
        inset: 0;
        background-color: rgba(0, 0, 0, 0.45);
        backdrop-filter: blur(6px);
        -webkit-backdrop-filter: blur(6px);
        transition: all 0.3s ease;
    }
    .modal {
        z-index: 1050;
        position: fixed;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 16px;
        overflow: hidden;
    }
    .modal-dialog {
        width: min(940px, calc(100vw - 36px));
        margin: 0;
    }
    .modal-content {
        max-height: min(74vh, 740px);
        border: 1px solid rgba(128, 128, 128, 0.2);
        border-radius: var(--radius-xl, 24px);
        overflow: hidden;
        display: flex;
        flex-direction: column;
    }
    .glass-modal {
        background-color: var(--app-bg);
        box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
    }
    .modal-header-shell {
        padding: 12px 20px;
        border-bottom: 1px solid var(--border-subtle);
        display: flex;
        flex-wrap: nowrap;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        background: color-mix(in srgb, var(--app-bg) 92%, var(--accent-soft));
    }
    .header-left {
        min-width: 0;
        display: flex;
        align-items: center;
        flex: 1 1 auto;
    }
    .header-right {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 8px;
        min-width: 0;
    }
    .header-right.with-controls {
        flex: 1 1 auto;
        flex-wrap: wrap;
    }
    .header-right.close-only {
        flex: 0 0 auto;
        flex-wrap: nowrap;
    }
    .recorded-chip {
        display: inline-flex;
        align-items: center;
        font-size: 0.78rem;
        font-weight: 600;
        color: var(--text-muted);
        background: var(--soft-bg-primary);
        border: 1px solid var(--border-subtle);
        border-radius: 999px;
        padding: 5px 10px;
        white-space: nowrap;
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .modal-header-shell .btn-close {
        margin-left: 2px;
        flex: 0 0 auto;
    }
    .preview-mode-switch {
        flex: 0 1 auto;
    }
    .preview-mode-switch .btn {
        font-size: 0.75rem;
        font-weight: 600;
    }
    .file-meta-row {
        display: flex;
        align-items: end;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
    }
    .file-meta-main {
        min-width: 0;
        flex: 1;
    }
    .ls-1 {
        letter-spacing: 0.05em;
    }
    .preview-container {
        min-height: 0;
        display: flex;
        flex-direction: column;
        flex: 1;
    }
    .preview-container .card {
        border: none;
        background: transparent;
        min-height: 0;
    }
    .preview-container .card-body {
        min-height: 0;
        display: flex;
        flex-direction: column;
    }
    .preview-scroll {
        min-height: 200px;
        max-height: calc(74vh - 235px);
        overflow: auto;
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-sm);
        background: var(--app-bg);
    }
    .preview-text {
        font-size: 0.86rem;
        line-height: 1.5;
        color: var(--text-primary);
        font-family: 'JetBrains Mono', 'Fira Code', monospace;
    }
    .preview-pre {
        min-width: max-content;
    }
    .preview-lines-scroll {
        padding: 8px 0;
    }
    .preview-lines-wrap {
        min-width: max-content;
        padding: 0 8px;
    }
    .preview-line {
        display: grid;
        grid-template-columns: 58px 1fr;
        align-items: start;
        column-gap: 10px;
        padding: 0 4px;
    }
    .preview-line-no {
        text-align: right;
        color: var(--text-muted);
        user-select: none;
        font-variant-numeric: tabular-nums;
        padding-right: 8px;
        border-right: 1px solid color-mix(in srgb, var(--border-subtle) 85%, transparent);
    }
    .preview-line-code {
        white-space: pre;
        color: var(--text-primary);
    }
    .diff-text {
        white-space: pre;
        padding: 8px 0;
        min-width: max-content;
    }
    .diff-line {
        display: grid;
        grid-template-columns: 56px 56px 20px 1fr;
        align-items: start;
        column-gap: 8px;
        margin: 0;
        padding: 1px 8px;
        border-radius: 0;
        border-top: 1px solid transparent;
        border-bottom: 1px solid transparent;
    }
    .diff-gutter {
        color: var(--text-muted);
        text-align: right;
        user-select: none;
        font-variant-numeric: tabular-nums;
    }
    .diff-sign {
        text-align: center;
        user-select: none;
    }
    .diff-code {
        white-space: pre;
        overflow-wrap: normal;
    }
    .diff-add {
        background: color-mix(in srgb, var(--bs-success-bg-subtle) 70%, transparent);
        border-top-color: var(--bs-success-border-subtle);
        border-bottom-color: var(--bs-success-border-subtle);
        color: var(--bs-success-text-emphasis);
    }
    .diff-remove {
        background: color-mix(in srgb, var(--bs-danger-bg-subtle) 70%, transparent);
        border-top-color: var(--bs-danger-border-subtle);
        border-bottom-color: var(--bs-danger-border-subtle);
        color: var(--bs-danger-text-emphasis);
    }
    .diff-context {
        background: transparent;
        color: var(--text-primary);
    }
    .diff-pill {
        display: inline-block;
        width: 12px;
        height: 12px;
        border-radius: 999px;
        margin-right: 6px;
        vertical-align: middle;
    }
    .diff-pill-add {
        background: var(--bs-success-bg-subtle);
        border: 1px solid var(--bs-success-border-subtle);
    }
    .diff-pill-remove {
        background: var(--bs-danger-bg-subtle);
        border: 1px solid var(--bs-danger-border-subtle);
    }
    .empty-state {
        background: var(--app-bg);
        border: 2px dashed var(--border-subtle);
    }
    .version-list-scroll {
        max-height: calc(74vh - 165px);
        overflow-y: auto;
        /* Scroll fade mask effect */
        mask-image: linear-gradient(to bottom, transparent 0%, black 5%, black 95%, transparent 100%);
        -webkit-mask-image: linear-gradient(to bottom, transparent 0%, black 5%, black 95%, transparent 100%);
        padding: 16px 8px;
        margin: -16px -8px; 
    }
    .version-list-scroll::-webkit-scrollbar {
        width: 6px;
    }
    .version-list-scroll::-webkit-scrollbar-track {
        background: transparent;
    }
    .version-list-scroll::-webkit-scrollbar-thumb {
        background-color: var(--border-subtle, #ccc);
        border-radius: 10px;
    }
    .version-row {
        background: var(--app-bg);
        border-color: var(--border-subtle) !important;
        transform: translateY(0);
    }
    .version-row:hover {
        background-color: var(--sidebar-hover);
        border-color: var(--accent) !important;
    }
    .version-row:hover .text-muted {
        color: var(--text-muted) !important;
    }
    .btn-preview-hover {
        transition: all 0.2s ease;
    }
    .version-row:hover .btn-preview-hover {
        background-color: var(--bs-primary);
        color: white;
    }
    .modal-body {
        padding: 16px 20px 14px;
        overflow: hidden;
        display: flex;
        flex-direction: column;
        min-height: 0;
    }
    .modal,
    .modal-body,
    .preview-scroll,
    .version-list-scroll {
        scroll-behavior: auto;
    }
    :global(body.locus-modal-open .view-wrapper),
    :global(body.locus-modal-open .app-container) {
        overflow: hidden !important;
    }
    @media (max-width: 992px) {
        .modal {
            padding: 12px;
        }
        .modal-dialog {
            width: calc(100vw - 24px);
        }
        .modal-header-shell.preview-header {
            flex-direction: column;
            align-items: stretch;
        }
        .modal-header-shell.history-header {
            flex-direction: row;
            align-items: center;
        }
        .header-right {
            width: 100%;
            justify-content: space-between;
        }
        .header-right.close-only {
            width: auto;
            justify-content: flex-end;
            flex: 0 0 auto;
        }
        .preview-scroll {
            max-height: calc(74vh - 255px);
        }
    }
    @media (max-width: 640px) {
        .modal {
            padding: 8px;
        }
        .modal-dialog {
            width: calc(100vw - 16px);
        }
        .modal-body {
            padding: 14px 12px 12px;
        }
        .header-right {
            flex-wrap: wrap;
            gap: 8px;
        }
        .preview-mode-switch {
            width: 100%;
        }
        .preview-mode-switch .btn {
            width: 50%;
        }
        .preview-scroll {
            min-height: 160px;
            max-height: calc(74vh - 285px);
        }
    }
</style>
