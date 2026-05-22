export function Thesis() {
  return (
    <section id="thesis" className="border-t border-slate-800/60 bg-slate-900/30">
      <div className="container mx-auto px-6 py-20 max-w-3xl prose prose-invert prose-slate">
        <h2>The thesis</h2>
        <p>
          Hermes-Agent by Nous Research is a remarkable platform for self-improving
          messaging agents — 27,000 lines of Python implementing channels, cron, skill
          systems, memory curation, sandbox backends, and trajectory tooling for model
          training.
        </p>
        <p>
          After studying it, I asked: <em>what if the engine is Claude Code?</em> Claude
          Code already has channels (Telegram, Discord, iMessage, custom), agent teams,
          server-hosted scheduled routines, agent view, Remote Control, mobile push, MCP,
          hooks, plugins, and auto-memory. The platform layer collapses.
        </p>
        <p>
          Hermes-Claude is the answer: a Claude Code plugin (~4,000 LOC) that fills the
          last 5% — a voice pipeline, a project orchestrator, a dashboard, and curated
          workflows. No API keys, just Claude Max subscription. Runs on a single Oracle
          Cloud free-tier VPS. The 90% you&apos;re looking at on this page <em>is</em>
          Claude Code, not me.
        </p>
        <p>
          Trading integrations come in V2; this V1 is the platform demonstration.
        </p>
      </div>
    </section>
  );
}
