export function DemoVideo() {
  return (
    <section className="container mx-auto px-6 py-20 max-w-5xl">
      <h2 className="text-2xl md:text-3xl font-bold tracking-tight">See it run</h2>
      <p className="mt-3 text-slate-400">
        A 60-second walkthrough is in progress for V1.5. In the meantime, the dashboard
        is live — login is restricted, but the public stats above pull from the running
        system.
      </p>
      <div className="mt-8 aspect-video rounded-lg border border-dashed border-slate-700 bg-slate-900/50 grid place-items-center text-slate-500 font-mono text-sm">
        [demo video placeholder]
      </div>
    </section>
  );
}
