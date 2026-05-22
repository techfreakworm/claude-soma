export function Footer() {
  return (
    <footer className="border-t border-slate-800/60">
      <div className="container mx-auto px-6 py-12 max-w-5xl flex flex-wrap gap-6 items-center justify-between text-sm text-slate-500">
        <div>
          Built by{" "}
          <a href="https://mayankgupta.in" className="text-slate-300 underline underline-offset-4">
            Mayank Gupta
          </a>
        </div>
        <div className="flex gap-4">
          <a href="https://github.com/techfreakworm/claude-soma">GitHub</a>
          <a href="/admin">Admin (auth required)</a>
        </div>
      </div>
    </footer>
  );
}
