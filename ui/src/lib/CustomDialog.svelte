<script>
  import { fade, scale } from 'svelte/transition';
  import { dialogStore } from '../dialogStore.js';
  import Fa from 'svelte-fa';
  import { 
    faCircleInfo, 
    faTriangleExclamation, 
    faCircleQuestion, 
    faCircleXmark 
  } from '@fortawesome/free-solid-svg-icons';

  $: ({ isOpen, title, message, type, confirmLabel, cancelLabel, onConfirm, onCancel } = $dialogStore);

  const icons = {
    info: faCircleInfo,
    warning: faTriangleExclamation,
    question: faCircleQuestion,
    error: faCircleXmark
  };

  const colors = {
    info: 'var(--accent)',
    warning: '#f59e0b',
    question: 'var(--accent)',
    error: 'var(--danger)'
  };

  const handleBackdropClick = (e) => {
    if (e.target.classList.contains('dialog-backdrop') && cancelLabel) {
      if (onCancel) onCancel();
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Escape' && cancelLabel) {
      if (onCancel) onCancel();
    }
  };
</script>

{#if isOpen}
  <div 
    class="dialog-backdrop" 
    on:click={handleBackdropClick}
    on:keydown={handleKeyDown}
    role="presentation"
    transition:fade={{ duration: 200 }}
  >
    <div 
      class="dialog-content card shadow-lg" 
      role="dialog"
      aria-modal="true"
      transition:scale={{ duration: 200, start: 0.95 }}
    >
      <div class="dialog-header px-4 pt-4 pb-2 border-0">
        <div class="d-flex align-items-center gap-3">
          <div class="dialog-icon" style="color: {colors[type]}">
            <Fa icon={icons[type]} size="lg" />
          </div>
          <h5 class="mb-0 fw-bold dialog-title">{title}</h5>
        </div>
      </div>
      
      <div class="dialog-body px-4 py-3">
        <p class="mb-0 text-muted" style="white-space: pre-wrap; line-height: 1.6;">
          {message}
        </p>
      </div>

      <div class="dialog-footer px-4 pb-4 pt-2 border-0 d-flex justify-content-end gap-2">
        {#if cancelLabel}
          <button 
            class="btn btn-outline-secondary px-4 py-2" 
            on:click={onCancel}
          >
            {cancelLabel}
          </button>
        {/if}
        <button 
          class="btn {type === 'error' ? 'btn-danger' : 'btn-primary'} px-4 py-2" 
          on:click={onConfirm}
        >
          {confirmLabel}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .dialog-backdrop {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.4);
    backdrop-filter: blur(4px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 9999;
    padding: 20px;
  }

  .dialog-content {
    width: 100%;
    max-width: 450px;
    background: var(--card-bg);
    color: var(--text-primary);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-xl);
    overflow: hidden;
  }

  .dialog-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
  }

  .dialog-title {
    color: var(--text-primary);
  }

  .dialog-footer .btn {
    font-size: 0.9rem;
    font-weight: 600;
  }
</style>
