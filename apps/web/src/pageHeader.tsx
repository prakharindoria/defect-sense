import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

interface PageHeaderValue { title: string; sub: string }

const EMPTY: PageHeaderValue = { title: "", sub: "" };

const Ctx = createContext<{
  header: PageHeaderValue;
  setHeader: (h: PageHeaderValue) => void;
} | null>(null);

/**
 * Mounted once per `Shell`, so the page title lives in the header row next to
 * the nav (matching the design) without every page having to know where the
 * header actually renders. A page calls `usePageTitle` once; `Nav` reads it.
 */
export function PageHeaderProvider({ children }: { children: ReactNode }) {
  const [header, setHeader] = useState<PageHeaderValue>(EMPTY);
  return <Ctx.Provider value={{ header, setHeader }}>{children}</Ctx.Provider>;
}

export function usePageHeaderValue(): PageHeaderValue {
  return useContext(Ctx)?.header ?? EMPTY;
}

/** Call once near the top of a page component to set the header's title/sub. */
export function usePageTitle(title: string, sub: string): void {
  // Depend on the setter itself, not the context object: `useState`
  // guarantees `setHeader`'s identity is stable across renders, but the
  // `{ header, setHeader }` object `PageHeaderProvider` hands out is a fresh
  // literal every render (including the one this effect's own call causes).
  // Depending on that whole object turns this into an infinite loop --
  // effect fires, sets state, provider re-renders with a new context object,
  // effect sees a "changed" dependency and fires again.
  const setHeader = useContext(Ctx)?.setHeader;
  useEffect(() => {
    setHeader?.({ title, sub });
  }, [setHeader, title, sub]);
}
