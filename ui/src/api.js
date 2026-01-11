// api.js - Dedicated service for backend communication
const BASE_URL = 'http://127.0.0.1:8000';

export async function checkHealth() {
  try {
    const res = await fetch(`${BASE_URL}/health`);
    if (!res.ok) throw new Error('Network response was not ok');
    return await res.json();
  } catch (e) {
    console.error('API Error:', e);
    return { background_service: 'offline', error: e.message };
  }
}

export async function getWatchedPaths() {
  const res = await fetch(`${BASE_URL}/files/watched`);
  return await res.json();
}

export async function addWatchedPath(path) {
  const res = await fetch(`${BASE_URL}/files/watched`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path })
  });
  if (!res.ok) throw new Error('Failed to add path');
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

export async function getActivityTimeline(limit = 50) {
  const res = await fetch(`${BASE_URL}/activity/timeline?limit=${limit}`);
  return await res.json();
}

export async function getRecentFileEvents(limit = 50) {
  const res = await fetch(`${BASE_URL}/files/events?limit=${limit}`);
  return await res.json();
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

export async function getFileVersionContent(versionId) {
  const res = await fetch(`${BASE_URL}/files/versions/${versionId}/content`);
  if (!res.ok) throw new Error('Failed to fetch version content');
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
