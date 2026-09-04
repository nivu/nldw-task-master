"use client";

import { useEffect, useState, type DependencyList } from "react";

import { errorMessage } from "@/lib/api/portal";

/**
 * Load something from the API, and reload it on demand.
 *
 * Exists for two reasons beyond tidiness.
 *
 * First, the cancellation flag. Changing the month or the roster date fires a
 * new request before the previous one has landed, and without this a slow
 * earlier response can arrive last and overwrite the newer data — the calendar
 * silently showing August while the header says September.
 *
 * Second, `setState` inside an effect body triggers cascading renders and the
 * React compiler rejects it. Resolving the state inside a promise callback is
 * both what the rule asks for and what the race above needs.
 */
export function useAsync<T>(load: () => Promise<T>, deps: DependencyList) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;

    load()
      .then((value) => {
        if (cancelled) return;
        setData(value);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(errorMessage(err));
      });

    return () => {
      cancelled = true;
    };
    // `load` is intentionally excluded: it is a fresh closure on every render,
    // and including it would re-fetch forever. The caller declares what the
    // request actually depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return {
    data,
    error,
    setError,
    /** Re-run the loader — after a booking, a decision, an admin change. */
    reload: () => setNonce((value) => value + 1),
  };
}
