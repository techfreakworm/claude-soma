export function Architecture() {
  return (
    <section id="architecture" className="container mx-auto px-6 py-20 max-w-5xl">
      <h2 className="text-2xl md:text-3xl font-bold tracking-tight">Architecture</h2>
      <p className="mt-3 text-slate-400">
        One always-on machine. One persistent Claude session. A handful of MCP servers and
        skills. Everything else — channels, cron, teams, memory, mobile — is native to Claude Code.
      </p>
      <pre className="mt-8 overflow-x-auto rounded-lg bg-slate-900/80 p-6 text-xs leading-relaxed font-mono text-slate-300 border border-slate-800">
{`Telegram ──► claude --channels (OCI VPS, Max OAuth)
                │
                ├── voice_stt MCP ──► whisper.cpp
                ├── voice_tts MCP ──► piper
                ├── project_orchestrator MCP ──► spawns project-leads
                └── hermes_api MCP ──► FastAPI ──► claude.mayankgupta.in

Each project-lead is its own background claude session with its own
TeamCreate-instantiated team. The orchestrator never team-leads itself.`}
      </pre>
    </section>
  );
}
