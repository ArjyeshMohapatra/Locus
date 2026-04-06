import { writable } from 'svelte/store';

const initialState = [];
const MAX_ERROR_MESSAGES = 50;
const DEDUPE_WINDOW_MS = 4000;
const recentlySeenMessages = new Map();

export const errorMessages = writable(initialState);

export function addErrorMessage(message) {
  const normalized = String(message || '').trim();
  if (!normalized) return;

  const now = Date.now();
  const duplicateTimestamp = recentlySeenMessages.get(normalized);
  if (duplicateTimestamp && now - duplicateTimestamp < DEDUPE_WINDOW_MS) {
    return;
  }

  recentlySeenMessages.set(normalized, now);
  for (const [cachedMessage, cachedAt] of recentlySeenMessages.entries()) {
    if (now - cachedAt > DEDUPE_WINDOW_MS * 3) {
      recentlySeenMessages.delete(cachedMessage);
    }
  }

  const entry = {
    id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
    message: normalized,
    timestamp: new Date().toISOString()
  };
  errorMessages.update((items) => [entry, ...items].slice(0, MAX_ERROR_MESSAGES));
}

export function clearErrorMessages() {
  errorMessages.set([]);
}

export function removeErrorMessage(id) {
  errorMessages.update((items) => items.filter((item) => item.id !== id));
}
