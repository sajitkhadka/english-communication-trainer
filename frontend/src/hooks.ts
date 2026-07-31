import { useCallback, useEffect, useRef, useState } from "react";

interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Fetch on mount and whenever `deps` change. `reload` re-runs without clearing data. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fnRef
      .current()
      .then((value) => {
        if (cancelled) return;
        setData(value);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

/** Call `fn` on an interval. Used for the queue badge, where the state changes outside
 *  the browser: transcription finishes in the backend, feedback appears when the user
 *  runs the skill in a terminal. */
export function useInterval(fn: () => void, ms: number | null) {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (ms === null) return;
    const id = window.setInterval(() => fnRef.current(), ms);
    return () => window.clearInterval(id);
  }, [ms]);
}

/** Call `fn` whenever the tab regains focus. `/process-session` runs in a terminal, so
 *  switching back to the browser is exactly the moment the page is out of date. */
export function useRefreshOnFocus(fn: () => void) {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible") fnRef.current();
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);
}

export function useDocumentTitle(title: string) {
  useEffect(() => {
    document.title = `${title} · English Communication Trainer`;
  }, [title]);
}
