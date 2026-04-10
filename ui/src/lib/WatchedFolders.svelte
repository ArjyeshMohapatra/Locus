<svelte:options runes={false} />

<script>
  import { onMount } from 'svelte';
  import { open as openDialog } from '@tauri-apps/plugin-dialog';
  import {
    getWatchedPaths,
    addWatchedPath,
    relinkWatchedPath,
    removeWatchedPath,
    getTrackingExclusions,
    setTrackingExclusions
  } from '../api.js';
  import { showMessage, askForText, askQuestion } from '../dialogStore.js';
  import Fa from 'svelte-fa';
  import { faLink, faFolderPlus, faTrashCan } from '@fortawesome/free-solid-svg-icons';

  let paths = [];
  let newPathInput = "";
  let isTauriAvailable = false;
  let ignorePromptInput = null;
  let ignorePromptVisible = false;
  let ignorePromptProjectPath = '';
  let ignorePromptProjectName = '';
  let ignorePromptFileName = '';
  let ignorePromptEntries = [];
  let ignorePromptError = '';
  let ignorePromptResolver = null;

  const PROJECT_SCOPE_PREFIX = '@project=';
  const PROJECT_SCOPE_SEPARATOR = '::';

  const detectTauriRuntime = () => (
    typeof window !== 'undefined' && !!(window.__TAURI__ || window.__TAURI_INTERNALS__ || window.__TAURI_IPC__)
  );

  const parseIgnoreFileContent = (value) => {
    const entries = [];

    for (const rawLine of String(value || '').split(/\r?\n/)) {
      let line = rawLine.trim();
      if (!line) continue;

      if (line.startsWith('\\#') || line.startsWith('\\!')) {
        line = line.slice(1);
      } else if (line.startsWith('#')) {
        continue;
      }

      line = line.replace(/[\\/]+$/, '').trim();
      if (!line) continue;

      for (const token of line.split(/[\t,;]+/)) {
        const cleaned = token.trim();
        if (cleaned) {
          entries.push(cleaned);
        }
      }
    }

    return Array.from(new Set(entries));
  };

  const normalizeProjectScope = (value) =>
    String(value || '')
      .trim()
      .replace(/\\/g, '/')
      .replace(/\/+$/, '');

  const getProjectNameFromPath = (value) => {
    const normalized = normalizeProjectScope(value);
    if (!normalized) return 'project';
    const segments = normalized.split('/').filter(Boolean);
    return segments.at(-1) || normalized;
  };

  const buildScopedProjectRule = (projectPath, rawRule) => {
    const scope = normalizeProjectScope(projectPath);
    const value = String(rawRule || '').trim();
    if (!scope || !value) {
      return value;
    }

    if (value.startsWith('!')) {
      const rule = value.slice(1).trim();
      if (!rule) return '';
      return `!${PROJECT_SCOPE_PREFIX}${scope}${PROJECT_SCOPE_SEPARATOR}${rule}`;
    }

    return `${PROJECT_SCOPE_PREFIX}${scope}${PROJECT_SCOPE_SEPARATOR}${value}`;
  };

  async function handleIgnorePromptFileChange(event) {
    ignorePromptError = '';
    ignorePromptEntries = [];
    ignorePromptFileName = '';

    const file = event?.target?.files?.[0];
    if (!file) {
      return;
    }

    ignorePromptFileName = String(file.name || '').trim() || 'ignore file';

    try {
      const text = await file.text();
      ignorePromptEntries = parseIgnoreFileContent(text);
      if (ignorePromptEntries.length === 0) {
        ignorePromptError = 'The selected file has no usable rules.';
      }
    } catch (error) {
      ignorePromptEntries = [];
      ignorePromptError = `Failed to read ignore file: ${error?.message || error}`;
    }
  }

  function resetIgnorePromptState() {
    ignorePromptVisible = false;
    ignorePromptProjectPath = '';
    ignorePromptProjectName = '';
    ignorePromptFileName = '';
    ignorePromptEntries = [];
    ignorePromptError = '';
    if (ignorePromptInput) {
      ignorePromptInput.value = '';
    }
  }

  function resolveIgnorePrompt(result) {
    const resolver = ignorePromptResolver;
    ignorePromptResolver = null;
    resetIgnorePromptState();
    if (resolver) {
      resolver(result);
    }
  }

  function requestIgnorePromptForProject(projectPath) {
    return new Promise((resolve) => {
      ignorePromptResolver = resolve;
      ignorePromptVisible = true;
      ignorePromptProjectPath = String(projectPath || '').trim();
      ignorePromptProjectName = getProjectNameFromPath(ignorePromptProjectPath);
      ignorePromptFileName = '';
      ignorePromptEntries = [];
      ignorePromptError = '';
      if (ignorePromptInput) {
        ignorePromptInput.value = '';
      }
    });
  }

  function handleIgnorePromptSkip() {
    resolveIgnorePrompt({ action: 'skip' });
  }

  function handleIgnorePromptCancel() {
    resolveIgnorePrompt({ action: 'cancel' });
  }

  function handleIgnorePromptApply() {
    if (!ignorePromptFileName) {
      ignorePromptError = 'Please upload a .gitignore, .gignore, or .txt file first.';
      return;
    }

    if (ignorePromptEntries.length === 0) {
      ignorePromptError = 'The selected file has no usable rules.';
      return;
    }

    resolveIgnorePrompt({
      action: 'apply',
      entries: [...ignorePromptEntries],
      fileName: ignorePromptFileName
    });
  }

  async function applyUploadedIgnoreEntries(projectPath, rawEntries, fileName) {
    if (!Array.isArray(rawEntries) || rawEntries.length === 0) {
      return true;
    }

    const scopedEntries = rawEntries
      .map((entry) => buildScopedProjectRule(projectPath, entry))
      .filter(Boolean);

    if (scopedEntries.length === 0) {
      return true;
    }

    try {
      const current = await getTrackingExclusions();
      const existing = Array.isArray(current?.custom_exclusions)
        ? current.custom_exclusions
            .map((item) => String(item || '').trim())
            .filter(Boolean)
        : [];

      const merged = Array.from(new Set([...existing, ...scopedEntries]));
      await setTrackingExclusions(merged);

      const projectName = getProjectNameFromPath(projectPath);
      await showMessage(
        `Loaded ${scopedEntries.length} rule${scopedEntries.length === 1 ? '' : 's'} for project "${projectName}" from "${fileName}".`,
        'Ignore File Applied',
        'info'
      );
      return true;
    } catch (e) {
      const proceed = await askQuestion(
        `Could not apply ignore-file exclusions: ${e?.message || e}\n\nProceed with folder tracking anyway?`,
        'Ignore File Error',
        {
          type: 'warning',
          okLabel: 'Proceed Anyway',
          cancelLabel: 'Cancel'
        }
      );
      return !!proceed;
    }
  }

  async function addWatchedPathWithPrompt(pathToAdd) {
    const normalizedPath = String(pathToAdd || '').trim();
    if (!normalizedPath) {
      return false;
    }

    const promptResult = await requestIgnorePromptForProject(normalizedPath);
    if (!promptResult || promptResult.action === 'cancel') {
      return false;
    }

    if (promptResult.action === 'apply') {
      const applied = await applyUploadedIgnoreEntries(
        normalizedPath,
        promptResult.entries,
        promptResult.fileName
      );
      if (!applied) {
        return false;
      }
    }

    try {
      await addWatchedPath(normalizedPath);
      await loadPaths();
      return true;
    } catch (e) {
      await showMessage('Add path failed: ' + (e?.message || e), 'Error', 'error');
      return false;
    }
  }

  onMount(() => {
    loadPaths();
    // Check if Tauri is available.
    isTauriAvailable = detectTauriRuntime();
  });

  async function loadPaths() {
    try {
      paths = await getWatchedPaths();
    } catch (e) {
      console.error("Failed to load paths", e);
    }
  }

  async function handleRelink(oldPath) {
    let newPath = null;

    // 1. Try Tauri Dialog
    if (isTauriAvailable) {
       try {
          const selected = await openDialog({
            directory: true,
            multiple: false,
            title: `Select New Location for ${oldPath}`
          });

          if (selected) {
            newPath = Array.isArray(selected) ? selected[0] : selected;
          }

          if (newPath && newPath !== oldPath) {
            const shouldMoveFiles = await askQuestion(
              `Do you want Locus to MOVE the files on disk for you?\n\n` +
              `YES = I want Locus to move files from "${oldPath}" to "${newPath}".\n` +
              `NO = I have already moved them manually.`,
              'Relink Folder',
              { type: 'warning', okLabel: 'Yes, Move Files', cancelLabel: 'No, Already Moved' }
            );

            try {
              await relinkWatchedPath(oldPath, newPath, shouldMoveFiles);
              await showMessage(`Location updated successfully!`, 'Success');
              await loadPaths();
            } catch (e) {
              await showMessage("Relink failed: " + e.message, 'Error', 'error');
            }
          }
       } catch (err) {
         console.error("Tauri dialog error:", err);
       }
       return;
    }

    // Fallback for non-Tauri (dev/browser mode): keep dialog styling consistent.
    newPath = await askForText(
      `Enter the new location for this watched folder:\n${oldPath}`,
      'Relink Folder',
      {
        type: 'question',
        okLabel: 'Continue',
        cancelLabel: 'Cancel',
        inputLabel: 'New folder path',
        initialValue: oldPath,
        placeholder: '/absolute/path/to/new/location',
        maxLength: 260
      }
    );

    const cleanedNewPath = String(newPath || '').trim();
    if (cleanedNewPath && cleanedNewPath !== oldPath) {
      const shouldMoveFiles = await askQuestion(
        `Do you want Locus to MOVE the files on disk for you?\n\n` +
        `YES = I want Locus to move files from "${oldPath}" to "${cleanedNewPath}".\n` +
        `NO = I have already moved them manually.`,
        'Relink Folder',
        { type: 'warning', okLabel: 'Yes, Move Files', cancelLabel: 'No, Already Moved' }
      );
      try {
        await relinkWatchedPath(oldPath, cleanedNewPath, shouldMoveFiles);
        await showMessage(`Location updated successfully!`, 'Success');
        await loadPaths();
      } catch (e) {
        await showMessage("Relink failed: " + e.message, 'Error', 'error');
      }
    }
  }

  async function handleAdd(useNativeDialog = false) {
    if (useNativeDialog) {
      try {
        if (isTauriAvailable) {
          const selected = await openDialog({
            directory: true,
            multiple: false,
            title: "Select Folder to Track"
          });

          if (selected) {
            const pathToAdd = Array.isArray(selected) ? selected[0] : selected;
            await addWatchedPathWithPrompt(pathToAdd);
            return;
          }
        }
      } catch (err) {
        await showMessage("Native dialog failed: " + (err?.message || err), 'Error', 'error');
      }
    }

    // Fallback to text input
    if(newPathInput){
      const added = await addWatchedPathWithPrompt(newPathInput);
      if (added) {
        newPathInput = "";
      }
    }
  }

  async function handleRemove(pathEntry) {
    const confirmed = await askQuestion(
      `Remove watched folder?\n\nPath: "${pathEntry.path}"\n\nThis will also delete tracked history, events, versions, and queued backup records for this folder.`,
      'Remove Watched Folder',
      { type: 'warning', okLabel: 'Remove', cancelLabel: 'Cancel' }
    );

    if (!confirmed) return;

    try {
      await removeWatchedPath(pathEntry.id);
      await showMessage('Watched folder and tracked data removed.', 'Success');
      await loadPaths();
    } catch (e) {
      await showMessage('Remove failed: ' + e.message, 'Error', 'error');
    }
  }
</script>

<div class="card mb-0">
  <div class="card-header d-flex align-items-center justify-content-between">
    <h5 class="card-title mb-0">Watched Folders</h5>
    <span class="badge-soft badge-soft-secondary">{paths.length} Total</span>
  </div>
  <div class="card-body">
    {#if paths.length === 0}
      <p class="text-muted">No folders being watched yet.</p>
    {:else}
      <ul class="list-group list-group-flush">
        {#each paths as p (p.id ?? p.path)}
          <li class="list-group-item d-flex justify-content-between align-items-center px-0">
            <span class="text-break me-2">{p.path}</span>
            <div class="d-flex align-items-center gap-3">
               <button
                  class="btn btn-sm btn-outline-secondary"
                  title="Relink Path (Move History)"
                  on:click={() => handleRelink(p.path)}
                >
                    <Fa icon={faLink} aria-hidden="true"/>
               </button>
              <button
                class="btn btn-sm btn-outline-danger"
                title="Remove Watched Folder"
                on:click={() => handleRemove(p)}
               >
                  <Fa icon={faTrashCan} aria-hidden="true"/>
              </button>
               <span class="badge-soft {p.is_active ? 'badge-soft-success' : 'badge-soft-secondary'}">
                  {p.is_active ? 'Active' : 'Missing'}
               </span>
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
  <div class="card-footer">
    <div class="d-grid gap-2">
      {#if isTauriAvailable}
        <button class="btn btn-primary" type="button" on:click={() => handleAdd(true)}>
          <Fa icon={faFolderPlus} class="me-1" aria-hidden="true"/>Choose Folder
        </button>
      {/if}

      <div class="input-group">
        <input
          type="text"
          class="form-control"
          placeholder="{isTauriAvailable ? 'Or enter path manually...' : 'Enter folder path...'}"
          bind:value={newPathInput}
          on:keydown={(e) => e.key === 'Enter' && handleAdd(false)}
        />
        <button class="btn btn-outline-secondary" type="button" on:click={() => handleAdd(false)}>
          Add
        </button>
      </div>

      {#if ignorePromptVisible}
        <div class="settings-note border rounded-3 p-3">
          <div class="fw-semibold mb-1">Project Ignore File</div>
          <div class="small text-muted mb-2">
            Upload a .gitignore, .gignore, or .txt file for <strong>{ignorePromptProjectName}</strong> before adding this watched folder.
          </div>
          <div class="input-group mb-2">
            <input
              type="file"
              class="form-control"
              accept=".gitignore,.gignore,.txt,text/plain"
              bind:this={ignorePromptInput}
              on:change={handleIgnorePromptFileChange}
            />
          </div>

          {#if ignorePromptFileName}
            <div class="form-text mb-2">
              Selected: {ignorePromptFileName}
              {#if ignorePromptEntries.length > 0}
                ({ignorePromptEntries.length} rule{ignorePromptEntries.length === 1 ? '' : 's'} parsed)
              {/if}
            </div>
          {/if}

          {#if ignorePromptError}
            <div class="small text-danger mb-2">{ignorePromptError}</div>
          {/if}

          <div class="d-flex gap-2 justify-content-end">
            <button class="btn btn-outline-secondary" type="button" on:click={handleIgnorePromptSkip}>
              Skip File
            </button>
            <button class="btn btn-outline-danger" type="button" on:click={handleIgnorePromptCancel}>
              Cancel Add
            </button>
            <button class="btn btn-primary" type="button" on:click={handleIgnorePromptApply}>
              Apply File And Continue
            </button>
          </div>
        </div>
      {/if}
    </div>
  </div>
</div>
