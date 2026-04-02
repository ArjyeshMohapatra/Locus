// api.js - Dedicated service for backend communication
const DEFAULT_BASE_URL = 'http://127.0.0.1:8000';
const DEFAULT_REQUEST_TIMEOUT_MS = 15000;

function isTauriRuntime() {
  return typeof window !== 'undefined' && !!(window.__TAURI__ || window.__TAURI_IPC__);
}

function resolveBaseUrl() {
  if (typeof window === 'undefined') {
    return DEFAULT_BASE_URL;
  }

  const fromGlobal = String(window.__LOCUS_BACKEND_URL || '').trim();
  if (fromGlobal) {
    return fromGlobal;
  }

  // Only trust persisted dynamic port data inside Tauri runtime.
  if (isTauriRuntime()) {
    try {
      const fromStorage = String(window.localStorage.getItem('locus-backend-url') || '').trim();
      if (fromStorage) {
        return fromStorage;
      }
    } catch {
      // localStorage might be unavailable in hardened runtime contexts.
    }
  }

  return DEFAULT_BASE_URL;
}

export const BASE_URL = {
  toString() {
    return resolveBaseUrl();
  }
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isTransientNetworkError(error) {
  const message = String(error?.message || '').toLowerCase();
  return (
    error instanceof TypeError ||
    message.includes('load failed') ||
    message.includes('failed to fetch') ||
    message.includes('networkerror') ||
    message.includes('network error') ||
    message.includes('ecconnrefused')
  );
}

async function fetchWithRetry(url, options = {}, { attempts = 1, retryDelayMs = 400 } = {}) {
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await fetch(url, options);
    } catch (error) {
      lastError = error;
      if (!isTransientNetworkError(error) || attempt >= attempts) {
        throw error;
      }
      await sleep(retryDelayMs);
    }
  }

  throw lastError || new Error('Request failed');
}

async function fetchWithTimeout(
  url,
  options = {},
  { timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS, attempts = 1, retryDelayMs = 400 } = {}
) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetchWithRetry(
      url,
      {
        ...options,
        signal: controller.signal
      },
      { attempts, retryDelayMs }
    );
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw new Error('Request timed out. Backend may be busy. Please try again.');
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

function toStartupHint(error, fallbackMessage) {
  if (isTransientNetworkError(error)) {
    return new Error('Local backend is still starting. Please wait a few seconds and try again.');
  }
  return new Error(error?.message || fallbackMessage);
}

async function resolveAuthBaseUrl() {
  if (typeof window === 'undefined' || !isTauriRuntime()) {
    return String(BASE_URL);
  }

  for (let attempt = 0; attempt < 20; attempt += 1) {
    const fromGlobal = String(window.__LOCUS_BACKEND_URL || '').trim();
    if (fromGlobal) {
      return fromGlobal;
    }
    await sleep(100);
  }

  try {
    const fromStorage = String(window.localStorage.getItem('locus-backend-url') || '').trim();
    if (fromStorage) {
      return fromStorage;
    }
  } catch {
    // Ignore localStorage access failures in hardened runtime contexts.
  }

  return DEFAULT_BASE_URL;
}

export async function checkHealth() {
  const requestHealth = async (baseUrl) => {
    const res = await fetch(`${baseUrl}/health`);
    if (!res.ok) throw new Error('Network response was not ok');
    return await res.json();
  };

  try {
    const resolvedBase = String(BASE_URL);
    try {
      return await requestHealth(resolvedBase);
    } catch (primaryError) {
      // In desktop runtime, never silently pivot to localhost:8000.
      // That can hit a different backend instance and cause auth state mismatch.
      if (resolvedBase === DEFAULT_BASE_URL || isTauriRuntime()) {
        throw primaryError;
      }

      const fallbackHealth = await requestHealth(DEFAULT_BASE_URL);

      if (typeof window !== 'undefined') {
        window.__LOCUS_BACKEND_URL = DEFAULT_BASE_URL;
        try {
          if (isTauriRuntime()) {
            window.localStorage.setItem('locus-backend-url', DEFAULT_BASE_URL);
          }
        } catch {
          // Ignore storage write failures in restricted contexts.
        }
      }

      return fallbackHealth;
    }
  } catch (e) {
    console.error('API Error:', e);
    return { background_service: 'offline', error: e.message };
  }
}

export async function getWatchedPaths() {
  const res = await fetch(`${BASE_URL}/files/watched`);
  return await res.json();
}

export async function getWatchedTree() {
  const res = await fetch(`${BASE_URL}/files/watched/tree`);
  if (!res.ok) throw new Error('Failed to fetch watched tree');
  return await res.json();
}

export async function addWatchedPath(path) {
  const res = await fetch(`${BASE_URL}/files/watched`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path })
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to add path');
  }
  return await res.json();
}

export async function removeWatchedPath(pathId) {
  const res = await fetch(`${BASE_URL}/files/watched/${pathId}`, {
    method: 'DELETE'
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to remove watched path');
  }
  return await res.json();
}

export async function relinkWatchedPath(oldPath, newPath, moveFiles = false) {
  const payload = {
    old_path: String(oldPath).trim(),
    new_path: String(newPath).trim(),
    move_files: !!moveFiles
  };
  
  const res = await fetch(`${BASE_URL}/files/watched/relink`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to relink path');
  }
  return await res.json();
}

export async function createCheckpointSession(payload = {}) {
  const res = await fetch(`${BASE_URL}/checkpoints/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {})
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to create checkpoint session');
  }
  return await res.json();
}

export async function listCheckpointSessions({ watchedPath = null, limit = 100 } = {}) {
  const url = new URL(`${BASE_URL}/checkpoints/sessions`);
  url.searchParams.append('limit', String(limit));
  if (watchedPath) {
    url.searchParams.append('watched_path', watchedPath);
  }

  const res = await fetch(url);
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to list checkpoint sessions');
  }
  return await res.json();
}

export async function getCheckpointSessionDetail(sessionId) {
  const res = await fetch(`${BASE_URL}/checkpoints/sessions/${sessionId}`);
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to fetch checkpoint session detail');
  }
  return await res.json();
}

export async function renameCheckpointSession(sessionId, name) {
  const res = await fetch(`${BASE_URL}/checkpoints/sessions/${sessionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to rename checkpoint session');
  }
  return await res.json();
}

export async function diffCheckpointSessions(fromSessionId, toSessionId, includeUnchanged = false) {
  const res = await fetch(`${BASE_URL}/checkpoints/sessions/diff`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from_session_id: fromSessionId,
      to_session_id: toSessionId,
      include_unchanged: !!includeUnchanged
    })
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to diff checkpoint sessions');
  }
  return await res.json();
}

export async function restoreCheckpointSession(sessionId, payload = {}) {
  const res = await fetch(`${BASE_URL}/checkpoints/sessions/${sessionId}/restore`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {})
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to restore checkpoint session');
  }
  return await res.json();
}

export async function getActivityTimeline(limit = 50) {
  const res = await fetch(`${BASE_URL}/activity/timeline?limit=${limit}`);
  return await res.json();
}

export async function getRecentFileEvents(limit = 50, path = null) {
  const url = new URL(`${BASE_URL}/files/events`);
  url.searchParams.append('limit', String(limit));
  if (path) {
    url.searchParams.append('path', path);
  }
  const res = await fetch(url);
  return await res.json();
}

export function subscribeFileEvents(onEvent) {
  const source = new EventSource(`${BASE_URL}/files/events/stream`);
  source.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onEvent(data);
    } catch (e) {
      console.error('Event parse error:', e);
    }
  };
  source.onerror = (err) => {
    console.error('EventSource error:', err);
  };
  return source;
}

export async function getFileVersions(path) {
  const url = new URL(`${BASE_URL}/files/versions`);
  url.searchParams.append('path', path);
  const res = await fetch(url);
  if (!res.ok) throw new Error('Failed to fetch versions');
  return await res.json();
}

export async function getCurrentFileVersion(path) {
  const url = new URL(`${BASE_URL}/files/current-version`);
  url.searchParams.append('path', path);
  const res = await fetch(url);
  if (!res.ok) throw new Error('Failed to fetch current version');
  return await res.json();
}

export async function getCurrentFileContent(path) {
  const url = new URL(`${BASE_URL}/files/current-content`);
  url.searchParams.append('path', path);
  const res = await fetch(url);
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to fetch current file content');
  }
  return await res.json();
}

export async function getFileVersionContent(versionId) {
  const res = await fetch(`${BASE_URL}/files/versions/${versionId}/content`);
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to fetch version content');
  }
  return await res.json();
}

export async function restoreFileVersion(versionId) {
  const res = await fetch(`${BASE_URL}/files/restore`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version_id: versionId })
  });
  if (!res.ok) throw new Error('Failed to restore version');
  return await res.json();
}

export async function getSecuritySettings() {
  const res = await fetch(`${BASE_URL}/settings/security`);
  if (!res.ok) throw new Error('Failed to fetch security settings');
  return await res.json();
}

export async function setSecuritySettings(enabled) {
  const res = await fetchWithTimeout(`${BASE_URL}/settings/security`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: !!enabled })
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to update security settings');
  }
  return await res.json();
}

export async function getTrackingExclusions() {
  const res = await fetchWithTimeout(`${BASE_URL}/settings/exclusions`);
  if (!res.ok) throw new Error('Failed to fetch tracking exclusions');
  return await res.json();
}

export async function setTrackingExclusions(exclusions) {
  const res = await fetchWithTimeout(`${BASE_URL}/settings/exclusions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ exclusions: exclusions || [] })
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to update tracking exclusions');
  }
  return await res.json();
}

export async function getSnapshotSettings() {
  const res = await fetchWithTimeout(`${BASE_URL}/settings/snapshots`);
  if (!res.ok) throw new Error('Failed to fetch snapshot settings');
  return await res.json();
}

export async function updateSnapshotSettings(updates) {
  const res = await fetchWithTimeout(`${BASE_URL}/settings/snapshots`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates || {})
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to update snapshot settings');
  }
  return await res.json();
}

export async function getRuntimeSettings() {
  const res = await fetchWithTimeout(`${BASE_URL}/settings/runtime`);
  if (!res.ok) throw new Error('Failed to fetch runtime settings');
  return await res.json();
}

export async function updateRuntimeSettings(updates) {
  const res = await fetchWithTimeout(`${BASE_URL}/settings/runtime`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates || {})
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to update runtime settings');
  }
  return await res.json();
}

export async function getAuthStatus() {
  const baseUrl = await resolveAuthBaseUrl();
  try {
    const res = await fetchWithRetry(`${baseUrl}/auth/status`, {}, { attempts: 12, retryDelayMs: 500 });
    if (!res.ok) throw new Error('Failed to fetch auth status');
    return await res.json();
  } catch (error) {
    throw toStartupHint(error, 'Failed to fetch auth status');
  }
}

export async function setupAuth(master_password) {
  const baseUrl = await resolveAuthBaseUrl();
  let res;
  try {
    res = await fetchWithRetry(
      `${baseUrl}/auth/setup`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ master_password })
      },
      { attempts: 10, retryDelayMs: 500 }
    );
  } catch (error) {
    throw toStartupHint(error, 'Failed to setup auth');
  }

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to setup auth');
  }

  return await res.json();
}

export async function unlockAuth(passphrase) {
  const baseUrl = await resolveAuthBaseUrl();
  let res;
  try {
    res = await fetchWithRetry(
      `${baseUrl}/auth/unlock`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passphrase })
      },
      { attempts: 8, retryDelayMs: 400 }
    );
  } catch (error) {
    throw toStartupHint(error, 'Failed to unlock');
  }

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to unlock');
  }

  return await res.json();
}

export async function lockAuth() {
  const res = await fetch(`${BASE_URL}/auth/lock`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to lock app');
  return await res.json();
}

export async function getDashboardSummary() {
  const res = await fetch(`${BASE_URL}/dashboard/summary`);
  if (!res.ok) throw new Error('Failed to fetch dashboard summary');
  return await res.json();
}

export async function resetAuth() {
  const res = await fetch(`${BASE_URL}/auth/reset`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to reset app data');
  return await res.json();
}

export async function getSnapshotHistory(payload = {}) {
  const res = await fetch(`${BASE_URL}/snapshots/history`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {})
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to fetch snapshot history');
  }
  return await res.json();
}

export async function executeSnapshotAction(actionType, value) {
  const res = await fetch(`${BASE_URL}/snapshots/execute-action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_type: actionType, value })
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to execute snapshot action');
  }
  return await res.json();
}

export async function deleteSnapshot(snapshotId) {
  const res = await fetch(`${BASE_URL}/snapshots/${snapshotId}`, {
    method: 'DELETE'
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to delete snapshot');
  }
  return await res.json();
}
