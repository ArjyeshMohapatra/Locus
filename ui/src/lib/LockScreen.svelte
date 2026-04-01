<script>
  import { afterUpdate, onMount, tick } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { setupAuth, unlockAuth, resetAuth } from '../api.js';
  import { askQuestion } from '../dialogStore.js';
  
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
  let unlockPasswordInput;
  let recoveryPasswordInput;

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

  onMount(async () => {
    await tick();
    focusPrimaryInput();
  });

  afterUpdate(() => {
    focusPrimaryInput();
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
      'Factory Reset LOCUS',
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
      <h2>{isSetupRequired ? 'Welcome to LOCUS' : 'LOCUS Locked'}</h2>
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
        <input type="password" class="form-control mb-3 lock-input" bind:this={setupPasswordInput} bind:value={password} placeholder="Master Password (min 12 chars)" on:keydown={(e) => e.key === 'Enter' && handleSetup()} disabled={isLoading} />
        <input type="password" class="form-control mb-3 lock-input" bind:value={confirmPassword} placeholder="Confirm Master Password" on:keydown={(e) => e.key === 'Enter' && handleSetup()} disabled={isLoading} />
        {#if errorMsg}<div class="text-danger small mb-3">{errorMsg}</div>{/if}
        <button class="btn btn-primary w-100" on:click={handleSetup} disabled={isLoading}>{isLoading ? 'Setting up...' : 'Create Vault'}</button>
      
      {:else if isForgotMode}
        <input type="password" class="form-control mb-3 lock-input" bind:this={recoveryPasswordInput} bind:value={password} placeholder="Enter Recovery Key" on:keydown={(e) => e.key === 'Enter' && handleUnlock()} disabled={isLoading} />
        {#if errorMsg}<div class="text-danger small mb-3">{errorMsg}</div>{/if}
        <button class="btn btn-primary w-100 mb-2" on:click={handleUnlock} disabled={isLoading}>{isLoading ? 'Unlocking...' : 'Unlock with Recovery Key'}</button>
        <button class="btn btn-outline-secondary w-100 mb-4" on:click={() => isForgotMode = false} disabled={isLoading}>Back to Login</button>
        <hr />
        <p class="text-muted small text-center mt-3">If you lost both, you must wipe all data.</p>
        <button class="btn btn-outline-danger w-100" on:click={handleReset} disabled={isLoading}>Factory Reset LOCUS</button>
      
      {:else}
        <input type="password" class="form-control mb-3 lock-input" bind:this={unlockPasswordInput} bind:value={password} placeholder="Master Password" on:keydown={(e) => e.key === 'Enter' && handleUnlock()} disabled={isLoading} />
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
    background: var(--surface);
    display: flex; align-items: center; justify-content: center; z-index: 10000;
  }
  .lock-card {
    background: var(--surface-elevated); padding: 40px; border-radius: 16px;
    box-shadow: 0 20px 40px rgba(0,0,0,0.1); width: 100%; max-width: 440px;
    border: 1px solid var(--border-subtle);
  }
  :global(.theme-dark) .lock-card { background: #1e293b; border-color: rgba(255,255,255,0.1); }
  .logo-box {
    width: 60px; height: 60px; background: var(--accent); color: white;
    font-size: 32px; font-weight: bold; border-radius: 14px;
    display: flex; align-items: center; justify-content: center; margin: 0 auto 20px;
  }
  .lock-header { text-align: center; margin-bottom: 30px; }
  .lock-header h2 { font-weight: 700; margin-bottom: 8px; font-size: 1.6rem; }
  .lock-input { padding: 12px 16px; font-size: 1rem; border-radius: 10px; }
  .recovery-box { background: #f1f5f9; padding: 16px; border-radius: 8px; word-break: break-all; text-align: center; }
  :global(.theme-dark) .recovery-box { background: #0f172a; color: #38bdf8; }
</style>
