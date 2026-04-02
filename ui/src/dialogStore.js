import { writable } from 'svelte/store';

export const dialogStore = writable({
  isOpen: false,
  title: '',
  message: '',
  messageScale: 1,
  type: 'info', // 'info', 'warning', 'error', 'question'
  confirmLabel: 'OK',
  cancelLabel: 'Cancel',
  inputEnabled: false,
  inputLabel: '',
  inputPlaceholder: '',
  inputValue: '',
  inputMaxLength: 120,
  onConfirm: null,
  onCancel: null
});

const normalizeMessageScale = (value) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 1;
  return Math.min(2, Math.max(1, parsed));
};

const normalizeInputMaxLength = (value, fallback = 120) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 1) return fallback;
  return Math.min(512, Math.floor(parsed));
};

/**
 * Show a simple message dialog (like alert/message)
 */
export const showMessage = (message, title = 'Notification', type = 'info', options = {}) => {
  return new Promise((resolve) => {
    dialogStore.set({
      isOpen: true,
      title,
      message,
      messageScale: normalizeMessageScale(options.messageScale),
      type,
      confirmLabel: 'OK',
      cancelLabel: '',
      inputEnabled: false,
      inputLabel: '',
      inputPlaceholder: '',
      inputValue: '',
      inputMaxLength: 120,
      onConfirm: () => {
        dialogStore.update(s => ({ ...s, isOpen: false }));
        resolve();
      },
      onCancel: null
    });
  });
};

/**
 * Show a confirmation dialog (like confirm/ask)
 */
export const askQuestion = (message, title = 'Confirm', options = {}) => {
  return new Promise((resolve) => {
    dialogStore.set({
      isOpen: true,
      title,
      message,
      messageScale: 1,
      type: options.type || 'question',
      confirmLabel: options.okLabel || 'Yes',
      cancelLabel: options.cancelLabel || 'No',
      inputEnabled: false,
      inputLabel: '',
      inputPlaceholder: '',
      inputValue: '',
      inputMaxLength: 120,
      onConfirm: () => {
        dialogStore.update(s => ({ ...s, isOpen: false }));
        resolve(true);
      },
      onCancel: () => {
        dialogStore.update(s => ({ ...s, isOpen: false }));
        resolve(false);
      }
    });
  });
};

/**
 * Show a text-input dialog (like prompt)
 */
export const askForText = (message, title = 'Input', options = {}) => {
  return new Promise((resolve) => {
    dialogStore.set({
      isOpen: true,
      title,
      message,
      messageScale: 1,
      type: options.type || 'question',
      confirmLabel: options.okLabel || 'Save',
      cancelLabel: options.cancelLabel || 'Cancel',
      inputEnabled: true,
      inputLabel: options.inputLabel || '',
      inputPlaceholder: options.placeholder || '',
      inputValue: String(options.initialValue ?? ''),
      inputMaxLength: normalizeInputMaxLength(options.maxLength, 120),
      onConfirm: (value) => {
        dialogStore.update(s => ({ ...s, isOpen: false }));
        resolve(value == null ? '' : String(value));
      },
      onCancel: () => {
        dialogStore.update(s => ({ ...s, isOpen: false }));
        resolve(null);
      }
    });
  });
};
