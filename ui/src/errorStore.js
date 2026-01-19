import { writable } from 'svelte/store';

const initialState = [];

export const errorMessages = writable(initialState);

export function addErrorMessage(message) {
  if (!message) return;
  const entry = {
    id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
    message,
    timestamp: new Date().toISOString()
  };
  errorMessages.update((items) => [entry, ...items].slice(0, 50));
}

export function clearErrorMessages() {
  errorMessages.set([]);
}

export function removeErrorMessage(id) {
  errorMessages.update((items) => items.filter((item) => item.id !== id));
}
