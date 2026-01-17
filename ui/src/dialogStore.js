import { writable } from 'svelte/store';

export const dialogStore = writable({
  isOpen: false,
  title: '',
  message: '',
  type: 'info', // 'info', 'warning', 'error', 'question'
  confirmLabel: 'OK',
  cancelLabel: 'Cancel',
  onConfirm: null,
  onCancel: null
});

/**
 * Show a simple message dialog (like alert/message)
 */
export const showMessage = (message, title = 'Notification', type = 'info') => {
  return new Promise((resolve) => {
    dialogStore.set({
      isOpen: true,
      title,
      message,
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
