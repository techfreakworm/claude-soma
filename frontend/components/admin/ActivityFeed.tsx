"use client";
import { useSse } from "@/lib/sse";
import { motion, AnimatePresence } from "framer-motion";

type Activity = { ts?: string; tool?: string; session?: string };

export function ActivityFeed() {
  const events = useSse("/api/events", 30);
  const items = events
    .filter((e) => e.type === "activity")
    .map((e) => e.data as Activity);

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
      <div className="text-xs uppercase tracking-wider text-slate-500 mb-3">
        Live activity
      </div>
      <ul className="space-y-1 font-mono text-xs text-slate-300 max-h-80 overflow-y-auto">
        <AnimatePresence initial={false}>
          {items.slice(-20).reverse().map((a, i) => (
            <motion.li key={`${a.ts}-${i}`}
                       initial={{ opacity: 0, x: -4 }}
                       animate={{ opacity: 1, x: 0 }}
                       exit={{ opacity: 0 }}>
              <span className="text-slate-500">{a.ts?.slice(11, 19)}</span>{" "}
              <span className="text-indigo-300">{a.tool}</span>{" "}
              <span className="text-slate-500">·</span>{" "}
              <span className="text-slate-400">{a.session?.slice(0, 8)}</span>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </div>
  );
}
