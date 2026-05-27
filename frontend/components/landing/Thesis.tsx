export function Thesis() {
  return (
    <section
      id="thesis"
      className="border-y border-slate-800/60 bg-slate-900/30 scroll-mt-20"
    >
      <div className="container mx-auto max-w-3xl px-5 py-16 sm:px-6 sm:py-24">
        <p className="text-sm font-semibold tracking-wider text-indigo-400 uppercase">
          The thesis
        </p>
        <h2 className="mt-3 text-2xl font-bold tracking-tight sm:text-3xl md:text-4xl">
          What if your engine is Claude Code?
        </h2>

        <div className="prose prose-invert prose-slate mt-6 max-w-none prose-p:text-slate-400 prose-strong:text-slate-200">
          <p>
            Hermes-Agent by Nous Research is a ~27,000-line platform for
            self-improving messaging agents — channels, cron, skills, memory
            curation, sandbox backends, and trajectory tooling. Studying it
            raises one question:{" "}
            <strong>
              what if your engine is Claude Code? How much do you actually need
              to build?
            </strong>
          </p>
          <p>
            The answer is a few thousand lines. Channels (Telegram, Discord,
            custom), agent teams, server-hosted scheduled routines, Remote
            Control, mobile push, MCP, hooks, plugins and auto-memory are all{" "}
            <strong>native</strong> to Claude Code. The platform layer
            collapses.
          </p>
          <p>
            Claude Soma is the missing slice — a voice pipeline, a project
            orchestrator, social posting, a dashboard, and curated workflows —
            riding Claude Code&apos;s native rails. Authed entirely through a
            Claude Max subscription, with no Anthropic API keys, running on a
            single Oracle Cloud Ubuntu ARM free-tier VPS.
          </p>
        </div>
      </div>
    </section>
  );
}
