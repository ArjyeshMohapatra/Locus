<svelte:options runes={false} />
<script>
  import { onDestroy, onMount, tick } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { setupAuth, unlockAuth, requestAuthReset, resetAuth } from '../api.js';
  import { askForText, askQuestion } from '../dialogStore.js';
  import Fa from 'svelte-fa';
  import { faCheck, faCopy, faEye, faEyeSlash } from '@fortawesome/free-solid-svg-icons';

  export let isSetupRequired = false;

  const dispatch = createEventDispatcher();

  let password = '';
  let confirmPassword = '';
  let errorMsg = '';
  let isLoading = false;
  let recoveryKey = '';
  let showRecovery = false;
  let recoveryCopied = false;
  let recoveryCopyTimer;
  let isForgotMode = false;
  let isResetMode = false;

  let setupPasswordInput;
  let setupConfirmPasswordInput;
  let unlockPasswordInput;
  let recoveryPasswordInput;

  let showSetupPassword = false;
  let showConfirmPassword = false;
  let showUnlockPassword = false;
  let showRecoveryPassword = false;
  let lastFocusMode = '';
  const RESET_CONFIRMATION_PHRASE = 'DELETE MY LOCUS DATA COMPLETELY';
  const RESET_COUNTDOWN_RADIUS = 32;
  const RESET_COUNTDOWN_CIRCUMFERENCE = 2 * Math.PI * RESET_COUNTDOWN_RADIUS;

  let isResetCountdownActive = false;
  let resetCountdownTotalSeconds = 0;
  let resetCountdownRemainingSeconds = 0;
  let resetCountdownInterval = null;
  let resetCountdownResolver = null;
  let resetCountdownOffset = RESET_COUNTDOWN_CIRCUMFERENCE;

  const resolveFocusMode = () => {
    if (showRecovery) return 'recovery';
    if (isSetupRequired) return 'setup';
    if (isResetMode) return 'reset';
    if (isForgotMode) return 'forgot';
    return 'unlock';
  };

  const focusPrimaryInput = () => {
    if (showRecovery || isResetMode) return;

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

  $: {
    const ratio = resetCountdownTotalSeconds > 0
      ? Math.max(0, Math.min(1, resetCountdownRemainingSeconds / resetCountdownTotalSeconds))
      : 0;
    resetCountdownOffset = RESET_COUNTDOWN_CIRCUMFERENCE * (1 - ratio);
  }

  const clearResetCountdownInterval = () => {
    if (resetCountdownInterval) {
      clearInterval(resetCountdownInterval);
      resetCountdownInterval = null;
    }
  };

  const completeResetCountdown = (didComplete) => {
    clearResetCountdownInterval();
    isResetCountdownActive = false;

    const resolve = resetCountdownResolver;
    resetCountdownResolver = null;
    if (resolve) {
      resolve(didComplete);
    }
  };

  const runResetCountdown = (seconds) => {
    const total = Math.max(0, Math.ceil(Number(seconds) || 0));
    if (total <= 0) {
      return Promise.resolve(true);
    }

    clearResetCountdownInterval();
    isResetCountdownActive = true;
    resetCountdownTotalSeconds = total;
    resetCountdownRemainingSeconds = total;

    return new Promise((resolve) => {
      resetCountdownResolver = resolve;

      resetCountdownInterval = setInterval(() => {
        resetCountdownRemainingSeconds = Math.max(0, resetCountdownRemainingSeconds - 1);
        if (resetCountdownRemainingSeconds <= 0) {
          completeResetCountdown(true);
        }
      }, 1000);
    });
  };

  const cancelResetCountdown = () => {
    if (!isResetCountdownActive) {
      return;
    }
    errorMsg = 'Reset cancelled during safety delay.';
    completeResetCountdown(false);
  };

  const clearResetCountdownSilently = () => {
    if (resetCountdownResolver) {
      completeResetCountdown(false);
    } else {
      clearResetCountdownInterval();
      isResetCountdownActive = false;
    }
  };

  const goToUnlockMode = () => {
    clearResetCountdownSilently();
    errorMsg = '';
    password = '';
    isForgotMode = false;
    isResetMode = false;
  };

  const goToRecoveryMode = () => {
    clearResetCountdownSilently();
    errorMsg = '';
    password = '';
    isForgotMode = true;
    isResetMode = false;
  };

  const goToResetMode = () => {
    clearResetCountdownSilently();
    errorMsg = '';
    password = '';
    isForgotMode = false;
    isResetMode = true;
  };

  onMount(() => {
    void focusForCurrentMode();
  });

  onDestroy(() => {
    if (recoveryCopyTimer) {
      clearTimeout(recoveryCopyTimer);
      recoveryCopyTimer = null;
    }

    if (resetCountdownResolver) {
      completeResetCountdown(false);
    } else {
      clearResetCountdownInterval();
    }
  });

  const copyRecoveryKey = async () => {
    errorMsg = '';
    const value = String(recoveryKey || '').trim();
    if (!value) {
      errorMsg = 'Recovery key is empty and cannot be copied.';
      return;
    }

    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        const area = document.createElement('textarea');
        area.value = value;
        area.setAttribute('readonly', 'readonly');
        area.style.position = 'fixed';
        area.style.top = '-9999px';
        area.style.left = '-9999px';
        document.body.appendChild(area);
        area.select();
        const copied = document.execCommand('copy');
        document.body.removeChild(area);
        if (!copied) {
          throw new Error('Copy command was blocked.');
        }
      }

      recoveryCopied = true;
      if (recoveryCopyTimer) {
        clearTimeout(recoveryCopyTimer);
      }
      recoveryCopyTimer = setTimeout(() => {
        recoveryCopied = false;
      }, 1500);
    } catch (error) {
      errorMsg = error?.message || 'Failed to copy recovery key.';
    }
  };

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
    if (isResetCountdownActive) {
      return;
    }

    errorMsg = '';

    const ok = await askQuestion(
      'Are you absolutely sure? This will permanently delete ALL your tracked data and snapshots.',
      'Factory Reset Locus',
      { type: 'danger', okLabel: 'Yes, Wipe Everything', cancelLabel: 'Cancel' }
    );
    if (!ok) {
      return;
    }

    let resetNonce = '';
    let cooldownSeconds = 0;
    try {
      const challenge = await requestAuthReset();
      resetNonce = String(challenge?.reset_nonce || '').trim();
      cooldownSeconds = Math.max(0, Number(challenge?.cooldown_seconds || 0));
    } catch (e) {
      errorMsg = e?.message || 'Failed to start reset confirmation.';
      return;
    }

    if (!resetNonce) {
      errorMsg = 'Reset challenge was invalid. Please try again.';
      return;
    }

    if (cooldownSeconds > 0) {
      const ready = await askQuestion(
        `A ${cooldownSeconds}-second safety delay is required before final wipe confirmation. Continue?`,
        'Safety Delay',
        { type: 'danger', okLabel: 'Start Delay', cancelLabel: 'Cancel' }
      );
      if (!ready) {
        return;
      }

      const countdownCompleted = await runResetCountdown(cooldownSeconds);
      if (!countdownCompleted) {
        return;
      }
      errorMsg = '';
    }

    const typedConfirmation = await askForText(
      `Type ${RESET_CONFIRMATION_PHRASE} to confirm permanent wipe.`,
      'Confirm Factory Reset',
      {
        type: 'danger',
        okLabel: 'Confirm Wipe',
        cancelLabel: 'Cancel',
        placeholder: RESET_CONFIRMATION_PHRASE,
        maxLength: 128,
        initialValue: ''
      }
    );

    if (typedConfirmation === null) {
      return;
    }

    if (String(typedConfirmation).trim() !== RESET_CONFIRMATION_PHRASE) {
      errorMsg = 'Reset cancelled: confirmation phrase did not match.';
      return;
    }

    isLoading = true;
    try {
      await resetAuth({
        confirmation: RESET_CONFIRMATION_PHRASE,
        resetNonce,
        finalConfirmed: true
      });
      window.location.reload();
    } catch (e) {
      errorMsg = e.message;
      isLoading = false;
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
        {:else if isResetMode}
          Factory reset permanently deletes all tracked data and snapshots.
        {:else if isForgotMode}
          Enter your Recovery Key to unlock your data.
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
        <div class="recovery-wrap">
          <div class="recovery-box">
            <code>{recoveryKey}</code>
          </div>
          <button class="btn recovery-copy-btn" on:click={copyRecoveryKey}>
            <Fa icon={recoveryCopied ? faCheck : faCopy} />
            <span>{recoveryCopied ? 'Copied' : 'Copy'}</span>
          </button>
        </div>
        {#if recoveryCopied}
          <div class="recovery-copy-toast">Recovery key copied.</div>
        {/if}
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
        <button class="btn btn-outline-danger w-100 mb-2" on:click={goToResetMode} disabled={isLoading}>Go to Factory Reset</button>
        <button class="btn btn-outline-secondary w-100" on:click={goToUnlockMode} disabled={isLoading}>Back to Login</button>

      {:else if isResetMode}
        <div class="alert alert-danger mb-3" style="font-size: 0.9rem;">
          <strong>Warning:</strong> This will permanently delete all watched paths, snapshots, versions, and backups.
        </div>
        {#if errorMsg}<div class="text-danger small mb-3">{errorMsg}</div>{/if}
        {#if isResetCountdownActive}
          <div class="reset-countdown-panel mb-3">
            <div
              class="reset-countdown-ring"
              role="img"
              aria-label={`Reset safety delay: ${resetCountdownRemainingSeconds} seconds remaining`}
            >
              <svg viewBox="0 0 80 80" aria-hidden="true" focusable="false">
                <circle class="reset-countdown-track" cx="40" cy="40" r={RESET_COUNTDOWN_RADIUS} />
                <circle
                  class="reset-countdown-progress"
                  cx="40"
                  cy="40"
                  r={RESET_COUNTDOWN_RADIUS}
                  stroke-dasharray={`${RESET_COUNTDOWN_CIRCUMFERENCE} ${RESET_COUNTDOWN_CIRCUMFERENCE}`}
                  stroke-dashoffset={resetCountdownOffset}
                />
              </svg>
              <span>{resetCountdownRemainingSeconds}</span>
            </div>
            <p class="text-muted small mb-2">Safety delay before final wipe confirmation.</p>
            <button class="btn btn-outline-danger w-100" on:click={cancelResetCountdown}>Cancel Reset</button>
          </div>
        {/if}
        <button class="btn btn-outline-danger w-100" on:click={handleReset} disabled={isLoading || isResetCountdownActive}>{isResetCountdownActive ? 'Safety Delay Running...' : 'Factory Reset Locus'}</button>
        <button class="btn btn-outline-secondary w-100 mt-2" on:click={goToRecoveryMode} disabled={isLoading}>Back to Recovery</button>
        <button class="btn btn-outline-secondary w-100 mt-2" on:click={goToUnlockMode} disabled={isLoading}>Back to Login</button>

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
        <button class="btn btn-link w-100 text-muted" on:click={goToRecoveryMode} disabled={isLoading} style="font-size: 0.9rem;">Forgot password?</button>
      {/if}
    </div>
  </div>
</div>

<style>
  .lock-screen-wrapper {
    --lock-top-offset: 0px;
    position: fixed;
    top: var(--lock-top-offset);
    left: 0;
    right: 0;
    bottom: 0;
    background: var(--app-bg);
    display: flex;
    align-items: center;
    align-items: safe center;
    justify-content: center;
    z-index: 980;
    padding: 18px;
    overflow-y: auto;
  }

  :global(body.has-custom-titlebar) .lock-screen-wrapper {
    --lock-top-offset: 40px;
    top: var(--lock-top-offset);
    padding-top: 18px;
  }

  :global(body:not(.has-custom-titlebar)) .lock-screen-wrapper {
    --lock-top-offset: 0px;
    top: var(--lock-top-offset);
    padding-top: 18px;
  }

  .lock-card {
    background: var(--surface-elevated);
    padding: 34px;
    border-radius: 14px;
    box-shadow: var(--shadow-lg);
    width: 100%;
    max-width: 440px;
    border: 1px solid var(--border-subtle);
    display: flex;
    flex-direction: column;
    max-height: calc(100dvh - var(--lock-top-offset) - 36px);
    overflow: hidden;
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

  .lock-body {
    overflow-y: auto;
    padding-right: 2px;
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

  .recovery-wrap {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .recovery-wrap .recovery-box {
    flex: 1;
  }

  .recovery-copy-btn {
    min-width: 0;
    height: 40px;
    padding: 0 14px;
    border-radius: 9px;
    border: 1px solid color-mix(in srgb, var(--accent) 45%, var(--border-subtle));
    background: color-mix(in srgb, var(--accent) 16%, var(--surface-elevated));
    color: var(--accent-strong);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    font-weight: 600;
    white-space: nowrap;
  }

  .recovery-copy-btn:hover {
    background: color-mix(in srgb, var(--accent) 24%, var(--surface-elevated));
    color: var(--accent-strong);
  }

  .recovery-copy-btn:focus-visible {
    box-shadow: 0 0 0 0.2rem color-mix(in srgb, var(--accent) 30%, transparent);
  }

  .recovery-copy-toast {
    margin-top: 10px;
    border: 1px solid color-mix(in srgb, var(--success) 45%, var(--border-subtle));
    background: color-mix(in srgb, var(--success) 18%, var(--surface-elevated));
    color: var(--text-primary);
    border-radius: 9px;
    padding: 8px 10px;
    font-size: 0.83rem;
    font-weight: 600;
  }

  @media (max-width: 560px) {
    .recovery-wrap {
      flex-direction: column;
    }

    .recovery-copy-btn {
      width: 100%;
    }
  }

  @media (max-height: 760px) {
    .lock-card {
      padding: 22px;
      max-height: calc(100dvh - var(--lock-top-offset) - 24px);
    }

    .lock-header {
      margin-bottom: 16px;
    }
  }

  :global(.theme-dark) .recovery-box {
    background: var(--surface-soft);
    color: var(--accent-strong);
  }

  :global(.theme-dark) .recovery-copy-btn {
    background: color-mix(in srgb, var(--accent) 22%, var(--surface-elevated));
    border-color: color-mix(in srgb, var(--accent) 50%, var(--border-subtle));
    color: var(--accent-strong);
  }

  .reset-countdown-panel {
    border: 1px solid color-mix(in srgb, var(--bs-danger, #dc3545) 40%, var(--border-subtle));
    border-radius: 12px;
    padding: 12px;
    background: color-mix(in srgb, var(--bs-danger, #dc3545) 12%, var(--surface-elevated));
  }

  .reset-countdown-ring {
    position: relative;
    width: 96px;
    height: 96px;
    margin: 0 auto 8px;
  }

  .reset-countdown-ring svg {
    width: 96px;
    height: 96px;
    transform: rotate(-90deg);
  }

  .reset-countdown-track {
    fill: none;
    stroke: color-mix(in srgb, var(--text-secondary) 35%, transparent);
    stroke-width: 8;
  }

  .reset-countdown-progress {
    fill: none;
    stroke: var(--bs-danger, #dc3545);
    stroke-width: 8;
    stroke-linecap: round;
    transition: stroke-dashoffset 0.95s linear;
  }

  .reset-countdown-ring span {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-primary);
    font-weight: 700;
    font-size: 1.35rem;
  }
</style>
