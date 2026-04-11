const TIMEZONE_SUFFIX_RE = /(?:[zZ]|[+\-]\d{2}:\d{2}|[+\-]\d{4})$/;

export const DATE_TIME_FORMATS = Object.freeze({
  SHORT_DATE_TIME: Object.freeze({
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true
  }),
  MEDIUM_DATE_TIME: Object.freeze({
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true
  }),
  LONG_DATE_TIME: Object.freeze({
    weekday: 'short',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: true
  })
});

export function normalizeDateInput(value) {
  if (value == null) return '';

  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return '';
    return value.toISOString();
  }

  if (typeof value === 'number') {
    return String(value);
  }

  const raw = String(value).trim();
  if (!raw) return '';

  if (TIMEZONE_SUFFIX_RE.test(raw)) {
    return raw;
  }

  const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
  return `${normalized}Z`;
}

export function parseDateInput(value) {
  if (value == null || value === '') return null;

  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : new Date(value.getTime());
  }

  if (typeof value === 'number') {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  const normalized = normalizeDateInput(value);
  if (!normalized) return null;

  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value, options = DATE_TIME_FORMATS.MEDIUM_DATE_TIME, fallback = '') {
  const date = parseDateInput(value);
  if (!date) return fallback;
  return new Intl.DateTimeFormat(undefined, options).format(date);
}
