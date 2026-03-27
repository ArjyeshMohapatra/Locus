<script>
  import { onMount } from 'svelte';
    import { fade } from 'svelte/transition';
                import { getFileVersions, restoreFileVersion, getFileVersionContent, getCurrentFileVersion, getCurrentFileContent } from '../api.js';
    import { showMessage, askQuestion } from '../dialogStore.js';
    import Fa from 'svelte-fa';
    import { faArrowLeft, faHistory, faUndo, faEye } from '@fortawesome/free-solid-svg-icons';

  export let filePath = null;
  export let onClose = () => {};

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

    const MAX_DIFF_TOTAL_LINES = 1600;

  // React to filePath changes
  $: if (filePath) {
      loadVersions();
      closePreview();
  }

  function closePreview() {
      selectedVersion = null;
      previewContent = null;
      previewLoading = false;
      currentContent = null;
      currentContentType = 'text';
      diffRows = [];
      diffTooLarge = false;
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

  $: showDiff = Boolean(
      selectedVersion &&
      currentVersionId !== selectedVersion.id &&
      previewType === 'text' &&
      currentContentType === 'text' &&
      !diffTooLarge
  );

    $: diffAddedCount = diffRows.filter((row) => row.type === 'add').length;
    $: diffRemovedCount = diffRows.filter((row) => row.type === 'remove').length;

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
<div class="modal show d-block" tabindex="-1" role="dialog" aria-modal="true" on:click|self={onClose} on:keydown={(e) => e.key === 'Escape' && onClose()}>
    <div class="modal-dialog modal-lg modal-dialog-centered" role="document">
    <div class="modal-content shadow-lg glass-modal">
      <div class="modal-header">
        <h5 class="modal-title">
            {#if selectedVersion}
                     <button class="btn btn-sm btn-outline-secondary me-2" on:click={closePreview}>
                         <Fa icon={faArrowLeft} class="me-1" aria-hidden="true"/> Back
                     </button>
                File Preview (V{selectedVersion.version_number})
            {:else}
                                <Fa icon={faHistory} class="me-2" aria-hidden="true"/>File History
                                {#if currentVersionNumber}
                                    <span class="ms-2 small text-muted">(Current: V{currentVersionNumber})</span>
                                {:else if currentHash}
                                    <span class="ms-2 small text-muted">(Current: unsnapped changes)</span>
                                {/if}
            {/if}
        </h5>
        <!-- <button type="button" class="btn-close" aria-label="Close modal" on:click={onClose}></button> -->
      </div>
      
      <div class="modal-body">
        <div class="mb-4">
            <small class="text-uppercase fw-bold ls-1" style="font-size: 0.7rem; color: var(--accent); opacity: 0.8;">File Path</small>
            <div class="text-break mt-1 fw-medium" style="color: var(--text-primary);">{filePath}</div>
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
                <div class="d-flex justify-content-between align-items-center mb-3 p-3 rounded-3 preview-header">
                    <div class="small text-muted fw-medium d-flex align-items-center gap-3">
                        <span>Recorded: {formatDate(selectedVersion.created_at)}</span>
                        {#if currentVersionId !== selectedVersion.id && previewType === 'text' && currentContentType === 'text' && !diffTooLarge}
                            <div class="btn-group btn-group-sm" role="group">
                                <input type="radio" class="btn-check" id="radioPreview" bind:group={viewMode} value="preview">
                                <label class="btn btn-outline-secondary" for="radioPreview">Raw Preview</label>

                                <input type="radio" class="btn-check" id="radioDiff" bind:group={viewMode} value="diff">
                                <label class="btn btn-outline-secondary" for="radioDiff">Diff vs Current</label>
                            </div>
                        {/if}
                    </div>
                    <button class="btn btn-primary btn-sm px-3" on:click={() => handleRestore(selectedVersion.id)}>
                        <Fa icon={faUndo} class="me-1" aria-hidden="true"/> Restore This Version
                    </button>
                </div>
                
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
                                <div class="m-0 p-3 overflow-auto preview-text diff-text">{#each diffRows as row}
<div class="diff-line diff-{row.type}">
    <span class="diff-gutter">{row.oldNo ?? ''}</span>
    <span class="diff-gutter">{row.newNo ?? ''}</span>
    <span class="diff-sign">{row.sign}</span>
    <span class="diff-code">{row.text}</span>
</div>{/each}</div>
                            {:else if diffTooLarge}
                                <div class="text-muted small px-3 pt-3">Diff is too large to render here. Showing raw historical content.</div>
                                <pre class="m-0 p-3 overflow-auto preview-text">{previewContent}</pre>
                            {:else}
                                <pre class="m-0 p-3 overflow-auto preview-text">{previewContent}</pre>
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
      
      <div class="modal-footer border-top-0 pt-0">
        <button type="button" class="btn btn-outline-secondary px-4" on:click={onClose}>Close</button>
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
        overflow-x: hidden;
        overflow-y: auto;
    }
    /* Ensure the dialog is well-constrained and centered */
    .modal-dialog {
        max-width: 900px;
        margin: auto;
    }
    .modal-content {
        max-height: 85vh;
        border: 1px solid rgba(128, 128, 128, 0.2);
        border-radius: var(--radius-xl, 24px);
        overflow: hidden;
    }
    .glass-modal {
        background-color: var(--app-bg);
        box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
    }
    .ls-1 {
        letter-spacing: 0.05em;
    }
    .preview-header {
        background: var(--app-bg);
        border: 1px solid var(--border-subtle);
    }
    .preview-text {
        max-height: 54vh; 
        font-size: 0.86rem; 
        line-height: 1.5;
        background: var(--app-bg); 
        color: var(--text-primary);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-sm);
        font-family: 'JetBrains Mono', 'Fira Code', monospace;
    }
    .diff-text {
        white-space: pre;
        padding-top: 8px !important;
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
        max-height: 48vh;
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
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.05) !important;
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
    /* Precise control for modal body height and scrolling */
    .modal-body {
        padding: 32px 32px 24px;
        overflow-y: visible;
    }
</style>
