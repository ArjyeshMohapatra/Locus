import { writable } from 'svelte/store';

export const dialogStore = writable({
  isOpen: false,
  title: '',
  message: '',
  messageScale: 1,
  type: 'info', // 'info', 'warning', 'error', 'question'
  confirmLabel: 'OK',
  cancelLabel: 'Cancel',
  onConfirm: null,
  onCancel: null
});

const normalizeMessageScale = (value) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 1;
  return Math.min(2, Math.max(1, parsed));
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
