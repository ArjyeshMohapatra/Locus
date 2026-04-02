<script>
  import { onMount, tick } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { setupAuth, unlockAuth, resetAuth } from '../api.js';
  import { askQuestion } from '../dialogStore.js';
  import Fa from 'svelte-fa';
  import { faEye, faEyeSlash } from '@fortawesome/free-solid-svg-icons';
  
  export let isSetupRequired = false;
  
  const dispatch = createEventDispatcher();
  
  let password = '';
  let confirmPassword = '';
  let errorMsg = '';
  let isLoading = false;
  let recoveryKey = '';
  let showRecovery = false;
  let isForgotMode = false;

  let setupPasswordInput;
  let setupConfirmPasswordInput;
  let unlockPasswordInput;
  let recoveryPasswordInput;

  let showSetupPassword = false;
  let showConfirmPassword = false;
  let showUnlockPassword = false;
  let showRecoveryPassword = false;
  let lastFocusMode = '';

  const resolveFocusMode = () => {
    if (showRecovery) return 'recovery';
    if (isSetupRequired) return 'setup';
    if (isForgotMode) return 'forgot';
    return 'unlock';
  };

  const focusPrimaryInput = () => {
    if (showRecovery) return;

    const target = isSetupRequired
      ? setupPasswordInput
      : isForgotMode
        ? recoveryPasswordInput
        : unlockPasswordInput;

    if (target && document.activeElement !== target) {
      target.focus();
    }
  };

  const focusForCurrentMode = async () => {
    await tick();
    focusPrimaryInput();
  };

  $: {
    const mode = resolveFocusMode();
    if (mode !== lastFocusMode) {
      lastFocusMode = mode;
      void focusForCurrentMode();
    }
  }

  const togglePasswordVisibility = async (field) => {
    if (field === 'setup') {
      showSetupPassword = !showSetupPassword;
      await tick();
      setupPasswordInput?.focus();
      return;
    }

    if (field === 'confirm') {
      showConfirmPassword = !showConfirmPassword;
      await tick();
      setupConfirmPasswordInput?.focus();
      return;
    }

    if (field === 'unlock') {
      showUnlockPassword = !showUnlockPassword;
      await tick();
      unlockPasswordInput?.focus();
      return;
    }

    if (field === 'recovery') {
      showRecoveryPassword = !showRecoveryPassword;
      await tick();
      recoveryPasswordInput?.focus();
    }
  };

  onMount(() => {
    void focusForCurrentMode();
  });
  
  const handleSetup = async () => {
    errorMsg = '';
    if (password.length < 12) {
      errorMsg = 'Master password must be at least 12 characters.';
      return;
    }
    if (password !== confirmPassword) {
      errorMsg = 'Passwords do not match.';
      return;
    }
    isLoading = true;
    try {
      const res = await setupAuth(password);
      recoveryKey = res.recovery_key;
      showRecovery = true;
    } catch (e) {
      const message = e?.message || 'Failed to setup auth';
      if (message.toLowerCase().includes('already setup')) {
        errorMsg = 'Vault already exists. Please unlock to continue.';
        confirmPassword = '';
        dispatch('setup-exists');
      } else {
        errorMsg = message;
      }
    } finally {
      isLoading = false;
    }
  };
  
  const finishSetup = () => {
    dispatch('unlocked');
  };
  
  const handleUnlock = async () => {
    errorMsg = '';
    if (!password) return;
    isLoading = true;
    try {
      await unlockAuth(password);
      dispatch('unlocked');
    } catch (e) {
      errorMsg = e.message;
    } finally {
      isLoading = false;
    }
  };
  
  const handleReset = async () => {
    const ok = await askQuestion(
      'Are you absolutely sure? This will permanently delete ALL your tracked data and snapshots.',
      'Factory Reset Locus',
      { type: 'danger', okLabel: 'Yes, Wipe Everything', cancelLabel: 'Cancel' }
    );
    if (ok) {
      isLoading = true;
      try {
        await resetAuth();
        window.location.reload();
      } catch (e) {
        errorMsg = e.message;
        isLoading = false;
      }
    }
  };
</script>

<div class="lock-screen-wrapper">
  <div class="lock-card">
    <div class="lock-header">
      <div class="logo-box">L</div>
      <h2>{isSetupRequired ? 'Welcome to Locus' : 'Locus Locked'}</h2>
      <p class="text-muted">
        {#if showRecovery}
          Save your Recovery Key!
        {:else if isSetupRequired}
          Create a Master Password to encrypt your data.
        {:else if isForgotMode}
          Forgot Password? Enter your Recovery Key or Factory Reset.
        {:else}
          Enter your Master Password to unlock your data.
        {/if}
      </p>
    </div>
    
    <div class="lock-body">
      {#if showRecovery}
        <div class="alert alert-warning" style="font-size: 0.9rem;">
          <strong>IMPORTANT:</strong> Save this key. It is the ONLY way to recover your data if you forget your master password.
        </div>
        <div class="recovery-box">
          <code>{recoveryKey}</code>
        </div>
        <button class="btn btn-primary w-100 mt-3" on:click={finishSetup}>I have saved it secretly</button>
      
      {:else if isSetupRequired}
        <div class="password-input-group mb-3">
          <input
            type={showSetupPassword ? 'text' : 'password'}
            class="form-control lock-input"
            bind:this={setupPasswordInput}
            bind:value={password}
            placeholder="Master Password (min 12 chars)"
            on:keydown={(e) => e.key === 'Enter' && handleSetup()}
            disabled={isLoading}
          />
          <button
            type="button"
            class="password-toggle-btn"
            aria-label={showSetupPassword ? 'Hide master password' : 'Show master password'}
            title={showSetupPassword ? 'Hide password' : 'Show password'}
            on:mousedown|preventDefault
            on:click={() => togglePasswordVisibility('setup')}
            disabled={isLoading}
          >
            <Fa icon={showSetupPassword ? faEye : faEyeSlash} />
          </button>
        </div>
        <div class="password-input-group mb-3">
          <input
            type={showConfirmPassword ? 'text' : 'password'}
            class="form-control lock-input"
            bind:this={setupConfirmPasswordInput}
            bind:value={confirmPassword}
            placeholder="Confirm Master Password"
            on:keydown={(e) => e.key === 'Enter' && handleSetup()}
            disabled={isLoading}
          />
          <button
            type="button"
            class="password-toggle-btn"
            aria-label={showConfirmPassword ? 'Hide confirm password' : 'Show confirm password'}
            title={showConfirmPassword ? 'Hide password' : 'Show password'}
            on:mousedown|preventDefault
            on:click={() => togglePasswordVisibility('confirm')}
            disabled={isLoading}
          >
            <Fa icon={showConfirmPassword ? faEye : faEyeSlash} />
          </button>
        </div>
        {#if errorMsg}<div class="text-danger small mb-3">{errorMsg}</div>{/if}
        <button class="btn btn-primary w-100" on:click={handleSetup} disabled={isLoading}>{isLoading ? 'Setting up...' : 'Create Vault'}</button>
      
      {:else if isForgotMode}
        <div class="password-input-group mb-3">
          <input
            type={showRecoveryPassword ? 'text' : 'password'}
            class="form-control lock-input"
            bind:this={recoveryPasswordInput}
            bind:value={password}
            placeholder="Enter Recovery Key"
            on:keydown={(e) => e.key === 'Enter' && handleUnlock()}
            disabled={isLoading}
          />
          <button
            type="button"
            class="password-toggle-btn"
            aria-label={showRecoveryPassword ? 'Hide recovery key' : 'Show recovery key'}
            title={showRecoveryPassword ? 'Hide recovery key' : 'Show recovery key'}
            on:mousedown|preventDefault
            on:click={() => togglePasswordVisibility('recovery')}
            disabled={isLoading}
          >
            <Fa icon={showRecoveryPassword ? faEye : faEyeSlash} />
          </button>
        </div>
        {#if errorMsg}<div class="text-danger small mb-3">{errorMsg}</div>{/if}
        <button class="btn btn-primary w-100 mb-2" on:click={handleUnlock} disabled={isLoading}>{isLoading ? 'Unlocking...' : 'Unlock with Recovery Key'}</button>
        <button class="btn btn-outline-secondary w-100 mb-4" on:click={() => isForgotMode = false} disabled={isLoading}>Back to Login</button>
        <hr />
        <p class="text-muted small text-center mt-3">If you lost both, you must wipe all data.</p>
        <button class="btn btn-outline-danger w-100" on:click={handleReset} disabled={isLoading}>Factory Reset Locus</button>
      
      {:else}
        <div class="password-input-group mb-3">
          <input
            type={showUnlockPassword ? 'text' : 'password'}
            class="form-control lock-input"
            bind:this={unlockPasswordInput}
            bind:value={password}
            placeholder="Master Password"
            on:keydown={(e) => e.key === 'Enter' && handleUnlock()}
            disabled={isLoading}
          />
          <button
            type="button"
            class="password-toggle-btn"
            aria-label={showUnlockPassword ? 'Hide password' : 'Show password'}
            title={showUnlockPassword ? 'Hide password' : 'Show password'}
            on:mousedown|preventDefault
            on:click={() => togglePasswordVisibility('unlock')}
            disabled={isLoading}
          >
            <Fa icon={showUnlockPassword ? faEye : faEyeSlash} />
          </button>
        </div>
        {#if errorMsg}<div class="text-danger small mb-3">{errorMsg}</div>{/if}
        <button class="btn btn-primary w-100 mb-3" on:click={handleUnlock} disabled={isLoading}>{isLoading ? 'Unlocking...' : 'Unlock'}</button>
        <button class="btn btn-link w-100 text-muted" on:click={() => { isForgotMode = true; errorMsg = ''; password = ''; }} disabled={isLoading} style="font-size: 0.9rem;">Forgot password?</button>
      {/if}
    </div>
  </div>
</div>

<style>
  .lock-screen-wrapper {
    position: fixed;
    top: 40px;
    left: 0;
    right: 0;
    bottom: 0;
    background: var(--app-bg);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10000;
    padding: 18px;
  }

  .lock-card {
    background: var(--surface-elevated);
    padding: 34px;
    border-radius: 14px;
    box-shadow: var(--shadow-lg);
    width: 100%;
    max-width: 440px;
    border: 1px solid var(--border-subtle);
  }

  :global(.theme-dark) .lock-card {
    background: var(--surface-elevated);
    border-color: var(--border-subtle);
  }

  .logo-box {
    width: 48px;
    height: 48px;
    background: var(--surface-soft);
    color: var(--accent);
    font-size: 22px;
    font-weight: 700;
    border-radius: 12px;
    border: 1px solid var(--border-subtle);
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 0 auto 18px;
  }

  .lock-header {
    text-align: center;
    margin-bottom: 24px;
  }

  .lock-header h2 {
    font-weight: 700;
    margin-bottom: 8px;
    font-size: 1.45rem;
    letter-spacing: -0.01em;
  }

  .lock-input {
    padding: 11px 14px;
    font-size: 0.96rem;
    border-radius: 10px;
  }

  .password-input-group {
    position: relative;
  }

  .password-input-group .lock-input {
    padding-right: 46px;
    margin-bottom: 0;
  }

  .password-toggle-btn {
    position: absolute;
    top: 50%;
    right: 10px;
    transform: translateY(-50%);
    border: 0;
    background: transparent;
    color: var(--text-secondary);
    width: 28px;
    height: 28px;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
  }
  .password-toggle-btn:hover:enabled {
    color: var(--text-primary);
  }

  .password-toggle-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .recovery-box {
    background: var(--surface-soft);
    border: 1px solid var(--border-subtle);
    padding: 14px;
    border-radius: 10px;
    word-break: break-all;
    text-align: center;
  }

  :global(.theme-dark) .recovery-box {
    background: var(--surface-soft);
    color: var(--accent-strong);
  }
</style>
