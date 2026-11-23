import { useCallback, useEffect, useState } from 'react';
import { errorText, request, type HistoryPage, type Report, type Summary } from './api';

let samplePromise: Promise<Report> | undefined;
export function getSample(): Promise<Report> {
  if (!samplePromise) {
    samplePromise = request<Report>('/sample-report.json').catch((error) => {
      samplePromise = undefined;
      throw error;
    });
  }
  return samplePromise;
}

export function useHistory(userId?: string) {
  const [items, setItems] = useState<Summary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [morePending, setMorePending] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setItems([]);
    setCursor(null);
    const firstPage: Promise<HistoryPage> = userId
      ? request<HistoryPage>('/api/analyses?limit=20', { signal: controller.signal })
      : getSample().then((sample) => ({ items: [sample], next_cursor: null }));
    firstPage
      .then((page) => {
        if (!controller.signal.aborted) {
          setItems(page.items);
          setCursor(page.next_cursor);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorText(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [userId, attempt]);

  const loadMore = useCallback(async () => {
    if (!cursor || morePending) return;
    setMorePending(true);
    setError('');
    try {
      const page = await request<HistoryPage>(
        '/api/analyses?limit=20&cursor=' + encodeURIComponent(cursor),
      );
      setItems((previous) => [
        ...previous,
        ...page.items.filter((entry) => !previous.some((item) => item.id === entry.id)),
      ]);
      setCursor(page.next_cursor);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setMorePending(false);
    }
  }, [cursor, morePending]);
  return {
    items,
    loading,
    cursor,
    error,
    reload: () => setAttempt((n) => n + 1),
    loadMore,
    morePending,
  };
}
