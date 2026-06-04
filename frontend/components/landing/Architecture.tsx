export function Architecture() {
  return (
    <section
      id="architecture"
      className="container mx-auto max-w-5xl px-5 py-16 sm:px-6 sm:py-24 scroll-mt-20"
    >
      <p className="text-sm font-semibold tracking-wider text-indigo-400 uppercase">
        Architecture
      </p>
      <h2 className="mt-3 text-2xl font-bold tracking-tight sm:text-3xl md:text-4xl">
        The Telegram session is an orchestrator, not a team-lead
      </h2>
      <p className="mt-4 max-w-3xl text-base text-slate-400 sm:text-lg">
        A request flows in on Telegram — text or voice. Fast work is answered
        inline; slow work is dispatched to a background subagent. The
        orchestrator spawns multiple independent project-leads, each in its own
        cgroup and each able to run its own agent team — sidestepping Claude
        Code&apos;s &ldquo;one team per lead&rdquo; constraint.
      </p>

      <div className="mt-8 min-w-0">
        <pre className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/80 p-4 font-mono text-xs leading-relaxed text-slate-300 sm:p-6">
          {`Telegram ──► claude --channels  (OCI VPS, Max OAuth)
                │
                ├── voice_stt MCP            ──► whisper.cpp
                ├── voice_tts MCP            ──► piper → opus
                ├── project_orchestrator MCP ──► spawns project-leads
                └── hermes_api MCP           ──► FastAPI ──► soma.<your-domain>

Each lead is spawned via 'sudo systemd-run' into its own transient
unit + dedicated tmux socket. It inherits every MCP server EXCEPT
Telegram, and is attachable from the Claude mobile app or
claude.ai/code via its Remote Control URL.`}
        </pre>
      </div>

      <dl className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
        {[
          {
            t: "Isolated",
            d: "Each lead lives in its own transient systemd unit + tmux server. A channel restart can't take a lead down.",
          },
          {
            t: "Inherited",
            d: "Leads get every MCP server except Telegram, so they can use voice, social and image tools without owning the chat.",
          },
          {
            t: "Reachable",
            d: "Every lead exposes a Remote Control URL — attach from the Claude mobile app or claude.ai/code at any time.",
          },
        ].map((c) => (
          <div
            key={c.t}
            className="rounded-xl border border-slate-800 bg-slate-900/40 p-4"
          >
            <dt className="text-sm font-semibold text-slate-100">{c.t}</dt>
            <dd className="mt-1.5 text-sm leading-relaxed text-slate-400">
              {c.d}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
