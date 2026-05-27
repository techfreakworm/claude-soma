"use client";
import { useState } from "react";
import { Menu, X } from "lucide-react";

const GITHUB_URL = "https://github.com/techfreakworm/claude-soma";

function GithubMark({ className }: { className?: string }) {
  return (
    <svg
      role="img"
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
    >
      <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.51 11.51 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222 0 1.606-.014 2.898-.014 3.293 0 .322.216.694.825.576C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
    </svg>
  );
}

const links = [
  { href: "#features", label: "Features" },
  { href: "#usage", label: "How you use it" },
  { href: "#architecture", label: "Architecture" },
  { href: "#thesis", label: "Thesis" },
];

export function Nav() {
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 border-b border-slate-800/60 bg-slate-950/80 backdrop-blur supports-[backdrop-filter]:bg-slate-950/70">
      <nav
        aria-label="Primary"
        className="container mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3 sm:px-6"
      >
        <a
          href="#top"
          className="flex items-center gap-2 font-semibold tracking-tight text-slate-100"
        >
          <span className="grid size-7 place-items-center rounded-md bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-400/30">
            <span className="text-sm font-bold">S</span>
          </span>
          <span className="text-base sm:text-lg">
            Claude <span className="text-indigo-400">Soma</span>
          </span>
        </a>

        <div className="hidden items-center gap-6 md:flex">
          {links.map((l) => (
            <a
              key={l.href}
              href={l.href}
              className="text-sm text-slate-400 transition-colors hover:text-slate-100"
            >
              {l.label}
            </a>
          ))}
          <div className="flex items-center gap-3">
            <a
              href="/admin"
              className="text-sm text-slate-400 transition-colors hover:text-slate-100"
            >
              Admin
            </a>
            <a
              href={GITHUB_URL}
              className="inline-flex h-9 items-center gap-2 rounded-lg bg-slate-100 px-3.5 text-sm font-medium text-slate-900 transition-colors hover:bg-white"
            >
              <GithubMark className="size-4" />
              GitHub
            </a>
          </div>
        </div>

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="mobile-menu"
          aria-label={open ? "Close menu" : "Open menu"}
          className="grid size-10 place-items-center rounded-lg text-slate-300 ring-1 ring-slate-700/70 transition-colors hover:bg-slate-800/60 md:hidden"
        >
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </nav>

      {open && (
        <div
          id="mobile-menu"
          className="border-t border-slate-800/60 bg-slate-950/95 md:hidden"
        >
          <div className="container mx-auto flex max-w-6xl flex-col gap-1 px-5 py-3">
            {links.map((l) => (
              <a
                key={l.href}
                href={l.href}
                onClick={() => setOpen(false)}
                className="flex min-h-11 items-center rounded-lg px-3 text-base text-slate-300 transition-colors hover:bg-slate-800/60 hover:text-slate-100"
              >
                {l.label}
              </a>
            ))}
            <a
              href="/admin"
              onClick={() => setOpen(false)}
              className="flex min-h-11 items-center rounded-lg px-3 text-base text-slate-300 transition-colors hover:bg-slate-800/60 hover:text-slate-100"
            >
              Admin
            </a>
            <a
              href={GITHUB_URL}
              onClick={() => setOpen(false)}
              className="mt-1 flex min-h-11 items-center justify-center gap-2 rounded-lg bg-slate-100 px-3 text-base font-medium text-slate-900 transition-colors hover:bg-white"
            >
              <GithubMark className="size-4" />
              View on GitHub
            </a>
          </div>
        </div>
      )}
    </header>
  );
}
