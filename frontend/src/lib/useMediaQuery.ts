import { useEffect, useState } from "react";

/** Whether a CSS media query matches, kept up to date (e.g. ``(min-width: 768px)``). */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const onChange = () => setMatches(list.matches);
    onChange();
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** A value remembered in this browser (a chosen tab, an opened section). */
export function useStored<T extends string>(key: string, initial: T, allowed?: readonly T[]) {
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = localStorage.getItem(key) as T | null;
      return saved !== null && (!allowed || allowed.includes(saved)) ? saved : initial;
    } catch {
      return initial;
    }
  });
  const set = (next: T) => {
    setValue(next);
    try {
      localStorage.setItem(key, next);
    } catch {
      // private window: the choice only lasts for this visit
    }
  };
  return [value, set] as const;
}
