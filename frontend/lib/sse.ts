"use client";

import { useEffect, useRef, useState } from "react";

export type SseEvent = { type: string; data: unknown };

export function useSse(path: string, max = 100): SseEvent[] {
  const [events, setEvents] = useState<SseEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource(path);
    esRef.current = es;

    const push = (type: string) => (msg: MessageEvent) => {
      let data: unknown = msg.data;
      try {
        data = JSON.parse(msg.data);
      } catch {
        /* keep as string */
      }
      setEvents((prev) => [...prev.slice(-(max - 1)), { type, data }]);
    };

    es.addEventListener("activity", push("activity"));
    es.addEventListener("ping", push("ping"));
    es.onerror = () => {
      // Browser auto-reconnects.
    };
    return () => es.close();
  }, [path, max]);

  return events;
}
