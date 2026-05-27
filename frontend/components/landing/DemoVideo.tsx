export function DemoVideo() {
  return (
    <section
      id="demo"
      className="container mx-auto px-5 sm:px-6 py-16 sm:py-24 max-w-5xl scroll-mt-20"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-2xl sm:text-3xl md:text-4xl font-bold tracking-tight">
            See it run
          </h2>
          <p className="mt-3 max-w-2xl text-slate-400">
            A 60-second tour of what Claude Soma is — narrated by the project&apos;s
            own <span className="text-slate-300">piper</span> voice, animated with the{" "}
            <span className="text-slate-300">hyperframes</span> skill it ships with.
          </p>
        </div>
        <span className="hidden shrink-0 rounded-full border border-slate-700 px-3 py-1 font-mono text-xs text-slate-400 sm:inline">
          ~56s · sound on
        </span>
      </div>

      <div className="mt-8 overflow-hidden rounded-xl border border-slate-800 bg-slate-900/50 shadow-2xl shadow-indigo-950/30">
        <video
          className="aspect-video w-full"
          controls
          playsInline
          preload="metadata"
          poster="/soma-intro-poster.jpg"
        >
          <source src="/soma-intro.mp4" type="video/mp4" />
          <track
            kind="captions"
            src="/soma-intro.vtt"
            srcLang="en"
            label="English"
            default
          />
          Your browser doesn&apos;t support embedded video — the tour is also on{" "}
          <a href="https://github.com/techfreakworm/claude-soma">GitHub</a>.
        </video>
      </div>
    </section>
  );
}
