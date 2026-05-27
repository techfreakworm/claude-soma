export function Footer() {
  return (
    <footer className="border-t border-slate-800/60">
      <div className="container mx-auto flex max-w-6xl flex-col gap-6 px-5 py-12 sm:px-6 md:flex-row md:items-center md:justify-between">
        <div className="space-y-2">
          <div className="flex items-center gap-2 font-semibold tracking-tight text-slate-200">
            <span className="grid size-6 place-items-center rounded-md bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-400/30">
              <span className="text-xs font-bold">S</span>
            </span>
            Claude Soma
          </div>
          <p className="text-sm text-slate-500">
            A body for Claude Code. MIT licensed. Built by{" "}
            <a
              href="https://mayankgupta.in"
              className="text-slate-300 underline underline-offset-4 transition-colors hover:text-slate-100"
            >
              Mayank Gupta
            </a>
            .
          </p>
        </div>
        <nav
          aria-label="Footer"
          className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-slate-400"
        >
          <a
            href="#features"
            className="transition-colors hover:text-slate-100"
          >
            Features
          </a>
          <a
            href="#architecture"
            className="transition-colors hover:text-slate-100"
          >
            Architecture
          </a>
          <a
            href="https://github.com/techfreakworm/claude-soma"
            className="transition-colors hover:text-slate-100"
          >
            GitHub
          </a>
          <a href="/admin" className="transition-colors hover:text-slate-100">
            Admin (auth required)
          </a>
        </nav>
      </div>
    </footer>
  );
}
