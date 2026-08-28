import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import type { RelayStatus } from "./types";

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

/** Whether this build is being served by the relay, and whether the PC is up.
 *
 *  The relay serves the *same* Vite build the PC serves (ADR 0006 §1), so the app works
 *  this out at runtime rather than at build time: `GET /api/relay/status` is answered
 *  only by the relay and 404s on the PC. A null status means "served by the PC", where
 *  every path behaves exactly as it always has.
 *
 *  The probe is cached in a module-level promise so the whole app pays for one request
 *  rather than one per component. */
let relayProbe: Promise<RelayStatus | null> | null = null;

function probeRelay(): Promise<RelayStatus | null> {
  relayProbe ??= api.relayStatus().catch(() => null);
  return relayProbe;
}

export function useRelay() {
  const [status, setStatus] = useState<RelayStatus | null>(null);
  const [ready, setReady] = useState(false);

  // Only worth re-asking once we know a relay is there: on the PC this route 404s on
  // every call, and the first probe already established that.
  const refresh = useCallback(() => {
    if (!relayProbe) return;
    void probeRelay().then((first) => {
      if (!first) return;
      api
        .relayStatus()
        .then(setStatus)
        .catch(() => {
          /* the relay itself is unreachable; keep the last known state */
        });
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void probeRelay().then((value) => {
      if (cancelled) return;
      setStatus(value);
      setReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return {
    status,
    /** True when this page is served by the relay rather than by the PC directly. */
    isRelay: status !== null,
    /** On the PC this is always true - it is serving the page, so it is up. */
    pcOnline: status === null ? true : status.pc_online,
    ready,
    refresh,
  };
}
