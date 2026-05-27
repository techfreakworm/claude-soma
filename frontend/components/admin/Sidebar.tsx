"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import {
  Menu,
  X,
  LayoutDashboard,
  Network,
  MessagesSquare,
  CalendarClock,
  Gauge,
  Brain,
  ScrollText,
  Home,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/admin", label: "Overview", icon: LayoutDashboard },
  { href: "/admin/projects", label: "Projects", icon: Network },
  { href: "/admin/conversations", label: "Conversations", icon: MessagesSquare },
  { href: "/admin/routines", label: "Routines", icon: CalendarClock },
  { href: "/admin/usage", label: "Usage", icon: Gauge },
  { href: "/admin/memory", label: "Memory", icon: Brain },
  { href: "/admin/logs", label: "Logs", icon: ScrollText },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/admin") return pathname === "/admin";
  return pathname === href || pathname.startsWith(href + "/");
}

function Brand() {
  return (
    <Link
      href="/"
      className="flex items-center gap-2 text-sm font-mono text-slate-300 hover:text-slate-100 transition-colors"
    >
      <span className="inline-flex size-6 items-center justify-center rounded bg-indigo-500/20 text-indigo-300">
        <Home className="size-3.5" />
      </span>
      <span className="font-semibold">Claude Soma</span>
      <span className="text-slate-600">/admin</span>
    </Link>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="space-y-1">
      {NAV.map((item) => {
        const active = isActive(pathname, item.href);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-3 px-3 py-2 rounded text-sm transition-colors",
              active
                ? "bg-indigo-500/15 text-indigo-200 font-medium"
                : "text-slate-300 hover:bg-slate-800/60 hover:text-slate-100",
            )}
          >
            <Icon className="size-4 shrink-0" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

export function Sidebar() {
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* Desktop / tablet: persistent left rail (md+) */}
      <aside className="hidden md:flex w-56 shrink-0 flex-col border-r border-slate-800 bg-slate-900/30 px-4 py-6 sticky top-0 h-screen">
        <div className="mb-6">
          <Brand />
        </div>
        <NavLinks />
      </aside>

      {/* Mobile: top bar with hamburger */}
      <header className="md:hidden sticky top-0 z-40 flex items-center justify-between border-b border-slate-800 bg-slate-950/90 px-4 py-3 backdrop-blur">
        <Brand />
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Open navigation menu"
          aria-expanded={open}
          className="inline-flex size-9 items-center justify-center rounded-lg border border-slate-800 text-slate-300 hover:bg-slate-800/60"
        >
          <Menu className="size-5" />
        </button>
      </header>

      {/* Mobile drawer */}
      <AnimatePresence>
        {open && (
          <div className="md:hidden">
            <motion.div
              className="fixed inset-0 z-50 bg-black/60"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setOpen(false)}
            />
            <motion.aside
              className="fixed inset-y-0 left-0 z-50 w-64 max-w-[80%] border-r border-slate-800 bg-slate-900 px-4 py-6 shadow-xl"
              initial={{ x: "-100%" }}
              animate={{ x: 0 }}
              exit={{ x: "-100%" }}
              transition={{ type: "tween", duration: 0.2 }}
            >
              <div className="mb-6 flex items-center justify-between">
                <Brand />
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  aria-label="Close navigation menu"
                  className="inline-flex size-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-800/60 hover:text-slate-100"
                >
                  <X className="size-5" />
                </button>
              </div>
              <NavLinks onNavigate={() => setOpen(false)} />
            </motion.aside>
          </div>
        )}
      </AnimatePresence>
    </>
  );
}
