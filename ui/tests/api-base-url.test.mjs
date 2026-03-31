import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BASE_URL,
  checkHealth,
  getWatchedPaths,
  listCheckpointSessions,
  renameCheckpointSession,
  diffCheckpointSessions,
  restoreCheckpointSession
} from '../src/api.js';

function createWindowMock({ globalUrl = '', storageUrl = '' } = {}) {
  return {
    __LOCUS_BACKEND_URL: globalUrl,
    __TAURI_IPC__: null,
    localStorage: {
      getItem(key) {
        if (key !== 'locus-backend-url') {
          return null;
        }
        return storageUrl;
      }
    }
  };
}

test('uses default backend URL when runtime override is unavailable', () => {
  const originalWindow = globalThis.window;
  delete globalThis.window;

  try {
    assert.equal(String(BASE_URL), 'http://127.0.0.1:8000');
  } finally {
    if (originalWindow !== undefined) {
      globalThis.window = originalWindow;
    }
  }
});

test('web runtime ignores localStorage backend URL when no global override is present', () => {
  const originalWindow = globalThis.window;
  globalThis.window = createWindowMock({
    globalUrl: '',
    storageUrl: 'http://127.0.0.1:8012'
  });

  try {
    assert.equal(String(BASE_URL), 'http://127.0.0.1:8000');
  } finally {
    if (originalWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }
  }
});

test('tauri runtime uses localStorage backend URL when global override is empty', () => {
  const originalWindow = globalThis.window;
  globalThis.window = {
    ...createWindowMock({
      globalUrl: '',
      storageUrl: 'http://127.0.0.1:8012'
    }),
    __TAURI_IPC__: {}
  };

  try {
    assert.equal(String(BASE_URL), 'http://127.0.0.1:8012');
  } finally {
    if (originalWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }
  }
});

test('global runtime backend URL takes precedence over localStorage', () => {
  const originalWindow = globalThis.window;
  globalThis.window = createWindowMock({
    globalUrl: 'http://127.0.0.1:8025',
    storageUrl: 'http://127.0.0.1:8012'
  });

  try {
    assert.equal(String(BASE_URL), 'http://127.0.0.1:8025');
  } finally {
    if (originalWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }
  }
});

test('API requests are sent to negotiated runtime backend port', async () => {
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;

  let seenUrl = '';
  globalThis.window = createWindowMock({
    globalUrl: 'http://127.0.0.1:8033',
    storageUrl: ''
  });

  globalThis.fetch = async (url) => {
    seenUrl = String(url);
    return {
      ok: true,
      async json() {
        return [];
      }
    };
  };

  try {
    await getWatchedPaths();
    assert.equal(seenUrl, 'http://127.0.0.1:8033/files/watched');
  } finally {
    if (originalWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }

    if (originalFetch === undefined) {
      delete globalThis.fetch;
    } else {
      globalThis.fetch = originalFetch;
    }
  }
});

test('checkHealth falls back to default backend when negotiated port is unreachable', async () => {
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;

  const storageState = { value: 'http://127.0.0.1:8033' };
  globalThis.window = {
    __LOCUS_BACKEND_URL: 'http://127.0.0.1:8033',
    __TAURI_IPC__: {},
    localStorage: {
      getItem(key) {
        return key === 'locus-backend-url' ? storageState.value : null;
      },
      setItem(key, value) {
        if (key === 'locus-backend-url') {
          storageState.value = String(value);
        }
      }
    }
  };

  globalThis.fetch = async (url) => {
    const textUrl = String(url);
    if (textUrl === 'http://127.0.0.1:8033/health') {
      throw new Error('ECONNREFUSED');
    }
    if (textUrl === 'http://127.0.0.1:8000/health') {
      return {
        ok: true,
        async json() {
          return { background_service: 'active' };
        }
      };
    }
    throw new Error(`Unexpected URL: ${textUrl}`);
  };

  try {
    const result = await checkHealth();
    assert.equal(result.background_service, 'active');
    assert.equal(globalThis.window.__LOCUS_BACKEND_URL, 'http://127.0.0.1:8000');
    assert.equal(storageState.value, 'http://127.0.0.1:8000');
  } finally {
    if (originalWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }

    if (originalFetch === undefined) {
      delete globalThis.fetch;
    } else {
      globalThis.fetch = originalFetch;
    }
  }
});

test('listCheckpointSessions includes watched_path and limit query params', async () => {
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;

  let seenUrl = '';
  globalThis.window = createWindowMock({
    globalUrl: 'http://127.0.0.1:8033',
    storageUrl: ''
  });

  globalThis.fetch = async (url) => {
    seenUrl = String(url);
    return {
      ok: true,
      async json() {
        return [];
      }
    };
  };

  try {
    await listCheckpointSessions({ watchedPath: '/tmp/project', limit: 25 });
    assert.equal(
      seenUrl,
      'http://127.0.0.1:8033/checkpoints/sessions?limit=25&watched_path=%2Ftmp%2Fproject'
    );
  } finally {
    if (originalWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }

    if (originalFetch === undefined) {
      delete globalThis.fetch;
    } else {
      globalThis.fetch = originalFetch;
    }
  }
});

test('renameCheckpointSession uses PATCH with name payload', async () => {
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;

  globalThis.window = createWindowMock({
    globalUrl: 'http://127.0.0.1:8033',
    storageUrl: ''
  });

  let method = '';
  let body = '';

  globalThis.fetch = async (_url, options = {}) => {
    method = String(options.method || '');
    body = String(options.body || '');
    return {
      ok: true,
      async json() {
        return { id: 1, name: 'release candidate' };
      }
    };
  };

  try {
    const result = await renameCheckpointSession(1, 'release candidate');
    assert.equal(method, 'PATCH');
    assert.equal(body, JSON.stringify({ name: 'release candidate' }));
    assert.equal(result.name, 'release candidate');
  } finally {
    if (originalWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }

    if (originalFetch === undefined) {
      delete globalThis.fetch;
    } else {
      globalThis.fetch = originalFetch;
    }
  }
});

test('diffCheckpointSessions posts session ids and include_unchanged', async () => {
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;

  globalThis.window = createWindowMock({
    globalUrl: 'http://127.0.0.1:8033',
    storageUrl: ''
  });

  let method = '';
  let body = '';

  globalThis.fetch = async (_url, options = {}) => {
    method = String(options.method || '');
    body = String(options.body || '');
    return {
      ok: true,
      async json() {
        return { summary: { added: 1, removed: 0, modified: 1 } };
      }
    };
  };

  try {
    const result = await diffCheckpointSessions(10, 11, true);
    assert.equal(method, 'POST');
    assert.equal(
      body,
      JSON.stringify({
        from_session_id: 10,
        to_session_id: 11,
        include_unchanged: true
      })
    );
    assert.equal(result.summary.added, 1);
  } finally {
    if (originalWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }

    if (originalFetch === undefined) {
      delete globalThis.fetch;
    } else {
      globalThis.fetch = originalFetch;
    }
  }
});

test('restoreCheckpointSession posts restore payload to session endpoint', async () => {
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;

  globalThis.window = createWindowMock({
    globalUrl: 'http://127.0.0.1:8033',
    storageUrl: ''
  });

  let seenUrl = '';
  let method = '';
  let body = '';

  globalThis.fetch = async (url, options = {}) => {
    seenUrl = String(url);
    method = String(options.method || '');
    body = String(options.body || '');
    return {
      ok: true,
      async json() {
        return { dry_run: true, summary: { planned: 1 } };
      }
    };
  };

  try {
    const result = await restoreCheckpointSession(42, {
      dry_run: true,
      conflict_strategy: 'rename',
      destination_root: '/tmp/project'
    });

    assert.equal(seenUrl, 'http://127.0.0.1:8033/checkpoints/sessions/42/restore');
    assert.equal(method, 'POST');
    assert.equal(
      body,
      JSON.stringify({
        dry_run: true,
        conflict_strategy: 'rename',
        destination_root: '/tmp/project'
      })
    );
    assert.equal(result.dry_run, true);
  } finally {
    if (originalWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }

    if (originalFetch === undefined) {
      delete globalThis.fetch;
    } else {
      globalThis.fetch = originalFetch;
    }
  }
});
