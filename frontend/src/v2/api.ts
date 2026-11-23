export interface User {
  id: string;
  name: string;
  email: string;
}
export type Status = 'queued' | 'processing' | 'completed' | 'failed';
export interface Summary {
  id: string;
  title: string;
  status: Status;
  source_type: 'transcript' | 'video';
  created_at: string;
  updated_at: string;
  keyword_count: number;
  word_count: number;
  duration_seconds?: number | null;
}
export interface Keyword {
  phrase: string;
  score: number;
  occurrences: number;
  evidence: string;
  start_seconds: number | null;
}
export interface Segment {
  text: string;
  start_seconds: number | null;
  end_seconds: number | null;
}
export interface Report extends Summary {
  stage: string;
  progress: number;
  error: string | null;
  transcript: string;
  transcript_revision?: number;
  segments: Segment[];
  keywords: Keyword[];
  speech_coverage?: {
    partial: boolean;
    recognized_chunks: number;
    total_chunks: number;
    unrecognized_ranges?: { start_seconds: number; end_seconds: number }[];
  } | null;
}
export interface HistoryPage {
  items: Summary[];
  next_cursor: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((entry: { msg?: string }) => entry.msg)
              .filter(Boolean)
              .join('. ')
          : '';
    if (response.status === 401 && !url.startsWith('/api/auth/')) {
      window.dispatchEvent(new Event('framewise-session-expired'));
    }
    throw new ApiError(response.status, message || 'The request could not be completed.');
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function jsonRequest<T>(url: string, method: string, data: unknown) {
  return request<T>(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

export function errorText(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return 'Could not connect. Check your connection and try again.';
}

export function reportDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(new Date(value));
}

export function timestamp(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  const mins = Math.floor(whole / 60);
  return `${mins}:${String(whole % 60).padStart(2, '0')}`;
}

export const isRunning = (status: Status) => status === 'queued' || status === 'processing';

export function reportHref(report: Summary, sample: boolean) {
  return sample ? '/analyses/sample' : '/analyses/' + encodeURIComponent(report.id);
}
