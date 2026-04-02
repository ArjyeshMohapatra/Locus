<script>
  import { tick } from 'svelte';
  import { fade, scale } from 'svelte/transition';
  import { dialogStore } from '../dialogStore.js';
  import Fa from 'svelte-fa';
  import { 
    faCircleInfo, 
    faTriangleExclamation, 
    faCircleQuestion, 
    faCircleXmark 
  } from '@fortawesome/free-solid-svg-icons';

  $: ({
    isOpen,
    title,
    message,
    messageScale,
    type,
    confirmLabel,
    cancelLabel,
    inputEnabled,
    inputLabel,
    inputPlaceholder,
    inputValue,
    inputMaxLength,
    onConfirm,
    onCancel
  } = $dialogStore);

  let promptValue = '';
  let promptInput = null;

  $: if (isOpen) {
    promptValue = inputEnabled ? String(inputValue || '') : '';
  }

  $: if (isOpen && inputEnabled) {
    tick().then(() => {
      if (!promptInput) return;
      promptInput.focus();
      promptInput.select();
    });
  }

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

    if (e.key === 'Enter' && inputEnabled) {
      e.preventDefault();
      handleConfirm();
    }
  };

  const handleConfirm = () => {
    if (!onConfirm) return;
    if (inputEnabled) {
      onConfirm(promptValue);
      return;
    }
    onConfirm();
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
      class="dialog-content" 
      style="--dialog-message-scale: {messageScale || 1};"
      role="dialog"
      aria-modal="true"
      transition:scale={{ duration: 200, start: 0.95 }}
    >
      <div class="dialog-header {type === 'danger' || type === 'error' ? 'dialog-header-danger' : ''}">
        <div class="dialog-header-main">
          <div class="dialog-icon" style="color: {type === 'danger' || type === 'error' ? '#fff' : colors[type]}">
            <Fa icon={icons[type] || icons.info} size="lg" />
          </div>
          <h5 class="dialog-title" style="color: {type === 'danger' || type === 'error' ? '#fff' : 'var(--text-primary)'};">{title}</h5>
        </div>
      </div>
      
      <div class="dialog-body">
        <p class="dialog-message" style="white-space: pre-wrap; line-height: 1.55;">
          {message}
        </p>

        {#if inputEnabled}
          {#if inputLabel}
            <label class="dialog-input-label" for="dialog-input-field">{inputLabel}</label>
          {/if}
          <input
            id="dialog-input-field"
            type="text"
            class="form-control dialog-input"
            bind:value={promptValue}
            bind:this={promptInput}
            maxlength={inputMaxLength}
            placeholder={inputPlaceholder || ''}
            on:keydown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleConfirm();
              }
            }}
          />
        {/if}
      </div>

      <div class="dialog-footer">
        {#if cancelLabel}
          <button 
            class="btn btn-outline-secondary dialog-btn" 
            on:click={onCancel}
          >
            {cancelLabel}
          </button>
        {/if}
        <button 
          class="btn {type === 'error' || type === 'danger' ? 'btn-danger' : 'btn-primary'} dialog-btn" 
          on:click={handleConfirm}
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
    background: rgba(15, 23, 42, 0.38);
    backdrop-filter: blur(3px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10500;
    padding: 20px;
    overflow: auto;
  }

  .dialog-content {
    width: 100%;
    max-width: 470px;
    max-height: calc(100vh - 40px);
    background: var(--card-bg);
    color: var(--text-primary);
    border: 1px solid var(--border-subtle);
    border-radius: 14px;
    box-shadow: var(--shadow-lg);
    overflow: hidden;
    margin: 0;
  }

  .dialog-header {
    padding: 0.88rem 1rem;
    border-bottom: 1px solid var(--border-subtle);
    background: var(--surface-soft);
  }

  .dialog-header-danger {
    background: var(--danger);
    border-bottom-color: color-mix(in srgb, var(--danger) 76%, #000);
  }

  .dialog-header-main {
    display: flex;
    align-items: center;
    gap: 0.55rem;
  }

  .dialog-body {
    overflow: auto;
    padding: 0.96rem 1rem 0.4rem;
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
  }

  .dialog-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
  }

  .dialog-title {
    color: var(--text-primary);
    font-size: calc(1rem * var(--dialog-message-scale, 1));
    margin: 0;
    font-weight: 700;
    line-height: 1.3;
  }

  .dialog-message {
    margin: 0;
    color: var(--text-muted);
    font-size: calc(0.95rem * var(--dialog-message-scale, 1));
  }

  .dialog-input-label {
    color: var(--text-primary);
    font-size: 0.82rem;
    font-weight: 600;
  }

  .dialog-input {
    min-height: 40px;
    border-radius: var(--ui-radius-control, 999px);
  }

  .dialog-footer {
    padding: 0.82rem 1rem 1rem;
    border-top: 1px solid var(--border-subtle);
    display: flex;
    justify-content: flex-end;
    gap: 0.45rem;
    flex-wrap: wrap;
  }

  .dialog-btn {
    min-width: 120px;
    font-size: 0.88rem;
    font-weight: 600;
  }

  @media (max-width: 560px) {
    .dialog-footer {
      flex-direction: column;
    }

    .dialog-btn {
      width: 100%;
    }
  }
</style>
