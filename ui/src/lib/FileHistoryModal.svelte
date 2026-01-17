<script>
  import { onMount } from 'svelte';
    import { fade } from 'svelte/transition';
        import { getFileVersions, restoreFileVersion, getFileVersionContent, getCurrentFileVersion } from '../api.js';
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
  let previewContent = null;
  let previewLoading = false;
  let previewType = 'text';

  // React to filePath changes
  $: if (filePath) {
      loadVersions();
      closePreview();
  }

  function closePreview() {
      selectedVersion = null;
      previewContent = null;
      previewLoading = false;
  }

  async function openPreview(version) {
      selectedVersion = version;
      previewLoading = true;
      error = null;
      try {
          const data = await getFileVersionContent(version.id);
          previewContent = data.content;
          previewType = data.type;
      } catch (e) {
          error = "Failed to load preview: " + e.message;
          selectedVersion = null; // Exit preview on error
      } finally {
          previewLoading = false;
      }
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
<div class="modal-backdrop fade show" role="button" on:click|self={onClose} on:keydown={(e) => e.key === 'Escape' && onClose()} tabindex="-1"></div>

<!-- Modal Dialog -->
<div class="modal fade show d-block" tabindex="-1" role="dialog" aria-modal="true" on:click|self={onClose} on:keydown={(e) => e.key === 'Escape' && onClose()}>
  <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable" role="document">
    <div class="modal-content shadow-lg">
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
                    <div class="small text-muted fw-medium">
                        Recorded: {formatDate(selectedVersion.created_at)}
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
                            <pre class="m-0 p-3 overflow-auto preview-text">{previewContent}</pre>
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
                <div class="list-group list-group-flush">
                    {#each versions as v}
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <!-- svelte-ignore a11y-no-static-element-interactions -->
                        <div 
                            class="list-group-item list-group-item-action d-flex justify-content-between align-items-center cursor-pointer version-row py-3"
                            on:click={() => openPreview(v)}
                            on:keydown={(e) => e.key === 'Enter' && openPreview(v)}
                            role="button"
                            tabindex="0"
                            title="Click to preview content"
                        >
                            <div>
                                <div class="d-flex align-items-center mb-1">
                                    <span class="badge-soft badge-soft-secondary me-3">V{v.version_number}</span>
                                    <span class="fw-semibold">{formatDate(v.created_at)}</span>
                                    {#if currentVersionId === v.id}
                                        <span class="ms-3 badge-soft badge-soft-success">current</span>
                                    {/if}
                                </div>
                                <div class="text-muted small">
                                    Size: {formatSize(v.file_size_bytes)}
                                    {#if v.file_hash}
                                    <span class="mx-2">•</span> <span class="text-truncate d-inline-block align-bottom" style="max-width: 250px;" title={v.file_hash}>Hash: {v.file_hash}</span>
                                    {/if}
                                </div>
                            </div>
                            <button 
                                class="btn btn-sm btn-outline-secondary rounded-pill px-3" 
                                on:click|stopPropagation={() => openPreview(v)}
                            >
                                <Fa icon={faEye} class="me-2" aria-hidden="true"/>Preview
                            </button>
                        </div>
                    {/each}
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
        background-color: rgba(255, 255, 255, 0.1);
    }
    .modal {
        z-index: 1050;
        overflow-x: hidden;
    }
    /* Ensure the dialog is well-constrained and centered */
    .modal-dialog {
        max-width: 850px;
        margin: auto auto;
        /* min-height: calc(100% - 20vh); */
    }
    .modal-content {
        max-height: 80vh;
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-xl);
        overflow: hidden;
    }
    .ls-1 {
        letter-spacing: 0.05em;
    }
    .preview-header {
        background: var(--app-bg);
        border: 1px solid var(--border-subtle);
    }
    .preview-text {
        max-height: 480px; 
        font-size: 0.9rem; 
        background: var(--app-bg); 
        color: var(--text-primary);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md);
        font-family: 'JetBrains Mono', 'Fira Code', monospace;
    }
    .empty-state {
        background: var(--app-bg);
        border: 2px dashed var(--border-subtle);
    }
    .version-row {
        background: transparent;
        border-bottom: 1px solid var(--border-subtle) !important;
        border-top: none !important;
    }
    .version-row:last-child {
        border-bottom: none !important;
    }
    .version-row:hover {
        background-color: var(--sidebar-hover);
        color: var(--text-primary);
    }
    .version-row:hover .text-muted {
        color: var(--text-muted) !important;
    }
    /* Precise control for modal body height and scrolling */
    .modal-body {
        padding: 24px;
        overflow-y: auto;
    }
</style>
