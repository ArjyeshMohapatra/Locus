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
    error: faCircleXmark,
    danger: faTriangleExclamation
  };

  const colors = {
    info: 'var(--accent)',
    warning: '#f59e0b',
    question: 'var(--accent)',
    error: 'var(--danger)',
    danger: 'white'
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
      class="dialog-content shadow-lg" 
      role="dialog"
      aria-modal="true"
      transition:scale={{ duration: 200, start: 0.95 }}
    >
      <div class="dialog-header px-4 py-3 border-bottom d-flex align-items-center justify-content-center {type === 'danger' || type === 'error' ? 'bg-danger text-white' : ''}">
        <div class="d-flex align-items-center gap-2">
          <div class="dialog-icon" style="color: {type === 'danger' || type === 'error' ? 'white' : colors[type]}">
            <Fa icon={icons[type] || icons.info} size="lg" />
          </div>
          <h5 class="mb-0 fw-bold dialog-title" style="color: {type === 'danger' || type === 'error' ? 'white' : 'var(--text-primary)'};">{title}</h5>
        </div>
      </div>
      
      <div class="dialog-body px-4 py-3">
        <p class="mb-0 text-muted" style="white-space: pre-wrap; line-height: 1.6;">
          {message}
        </p>
      </div>

      <div class="dialog-footer px-4 pb-4 pt-3 border-0 d-flex justify-content-between gap-2">
        {#if cancelLabel}
          <button 
            class="btn btn-outline-secondary px-4 py-2 flex-grow-1 fw-bold" 
            on:click={onCancel}
          >
            {cancelLabel}
          </button>
        {/if}
        <button 
          class="btn {type === 'error' || type === 'danger' ? 'btn-danger' : 'btn-primary'} px-4 py-2 flex-grow-1 fw-bold shadow-sm" 
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
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    backdrop-filter: blur(4px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10500;
    padding: 20px;
    overflow: auto;
  }

  .dialog-content {
    width: 100%;
    max-width: 450px;
    max-height: calc(100vh - 40px);
    background: var(--card-bg);
    color: var(--text-primary);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-xl);
    overflow: hidden;
    margin: 0;
  }

  .dialog-body {
    overflow: auto;
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
