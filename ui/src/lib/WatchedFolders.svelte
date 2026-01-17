<script>
  import { onMount } from 'svelte';
  import { getWatchedPaths, addWatchedPath, relinkWatchedPath } from '../api.js';
  import { showMessage, askQuestion } from '../dialogStore.js';
  import Fa from 'svelte-fa';
  import { faLink, faFolderPlus } from '@fortawesome/free-solid-svg-icons';

  let paths = [];
  let newPathInput = "";
  let isTauriAvailable = false;

  onMount(() => {
    loadPaths();
    // Check if Tauri is available
    isTauriAvailable = typeof window !== 'undefined' && window.__TAURI__;
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
          const { open } = await import('@tauri-apps/api/dialog');
          const selected = await open({
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

    // Fallback for non-Tauri (unlikely in production but good for dev)
    newPath = prompt(`Enter new location for:\n${oldPath}`, oldPath);
    if (newPath && newPath !== oldPath) {
      const shouldMoveFiles = confirm(`Do you want Locus to MOVE the files on disk for you?`);
      try {
        await relinkWatchedPath(oldPath, newPath, shouldMoveFiles);
        showMessage(`Location updated successfully!`, 'Success');
        await loadPaths();
      } catch (e) {
        showMessage("Relink failed: " + e.message, 'Error', 'error');
      }
    }
  }

  async function handleAdd(useNativeDialog = false) {
    if (useNativeDialog) {
      try {
        if (isTauriAvailable) {
          const { open } = await import('@tauri-apps/api/dialog');
          const selected = await open({
            directory: true,
            multiple: false,
            title: "Select Folder to Track"
          });
          
          if (selected) {
            const pathToAdd = Array.isArray(selected) ? selected[0] : selected;
            await addWatchedPath(pathToAdd);
            await loadPaths();
            return;
          }
        } 
      } catch (err) {
        await showMessage("Native dialog failed: " + err, 'Error', 'error');
      }
    }
    
    // Fallback to text input
    if(newPathInput){
      await addWatchedPath(newPathInput);
      newPathInput = "";
      await loadPaths();
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
        {#each paths as p}
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
    </div>
  </div>
</div>
