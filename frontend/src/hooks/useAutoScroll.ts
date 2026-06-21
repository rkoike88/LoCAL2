import { useEffect, useRef } from "react";

export function useAutoScroll(deps: readonly unknown[]) {
  const ref = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { ref.current?.scrollIntoView({ behavior: "smooth" }); }, deps);
  return ref;
}
