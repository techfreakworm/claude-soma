type Row = {
  say: string;
  voice?: boolean;
  does: string;
  get: string;
};

const rows: Row[] = [
  {
    say: "What am I working on?",
    voice: true,
    does: "portfolio-status skill",
    get: "Voice or text reply listing your repos and active project-leads",
  },
  {
    say: "Build a scraper for the F1 standings that tweets on change",
    does: "spawn-project → orchestrator spawns a cgroup-isolated lead running its own agent team",
    get: "A persistent f1-scraper lead with its own cwd, team, and Remote Control URL",
  },
  {
    say: "Tell f1-scraper to use httpx",
    does: "message-project (tmux send-keys into the lead's pane)",
    get: "The instruction lands in the lead; its reply is scraped back to you",
  },
  {
    say: "Post this to LinkedIn",
    does: "playwright-linkedin MCP session using the shared auth store",
    get: "Posted — no per-task login",
  },
  {
    say: "Draw the system architecture",
    does: "codex-image-gen → Codex CLI (ChatGPT sub, not Max)",
    get: "An image delivered as a Telegram photo",
  },
  {
    say: "Every weekday 8am IST, send me a brief",
    does: "schedule-routine",
    get: "A cloud routine running on Anthropic infra",
  },
];

function VoiceTag() {
  return (
    <span className="ml-2 inline-flex items-center rounded-full border border-indigo-400/30 bg-indigo-500/10 px-1.5 py-0.5 align-middle text-[10px] font-medium tracking-wide text-indigo-200 uppercase">
      voice
    </span>
  );
}

export function HowYouUse() {
  return (
    <section
      id="usage"
      className="border-y border-slate-800/60 bg-slate-900/30 scroll-mt-20"
    >
      <div className="container mx-auto max-w-6xl px-5 py-16 sm:px-6 sm:py-24">
        <div className="max-w-2xl">
          <p className="text-sm font-semibold tracking-wider text-indigo-400 uppercase">
            How you use it
          </p>
          <h2 className="mt-3 text-2xl font-bold tracking-tight sm:text-3xl md:text-4xl">
            One Telegram thread, the whole system
          </h2>
          <p className="mt-4 text-base text-slate-400 sm:text-lg">
            You talk to one chat. Behind it, a skill fires, work fans out to
            isolated leads, and the result comes back.
          </p>
        </div>

        {/* Mobile: stacked cards */}
        <ul className="mt-10 flex flex-col gap-4 md:hidden">
          {rows.map((r) => (
            <li
              key={r.say}
              className="rounded-xl border border-slate-800 bg-slate-950/40 p-4"
            >
              <p className="text-sm font-semibold text-slate-100">
                <span className="text-indigo-300">You:</span> &ldquo;{r.say}
                &rdquo;
                {r.voice && <VoiceTag />}
              </p>
              <dl className="mt-3 space-y-2 text-sm">
                <div>
                  <dt className="text-xs tracking-wider text-slate-500 uppercase">
                    What it does
                  </dt>
                  <dd className="mt-0.5 text-slate-300">{r.does}</dd>
                </div>
                <div>
                  <dt className="text-xs tracking-wider text-slate-500 uppercase">
                    What you get
                  </dt>
                  <dd className="mt-0.5 text-slate-300">{r.get}</dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>

        {/* md+ : table */}
        <div className="mt-10 hidden overflow-hidden rounded-xl border border-slate-800 md:block">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="bg-slate-900/60 text-xs tracking-wider text-slate-400 uppercase">
                <th scope="col" className="px-5 py-3 font-medium">
                  You say (Telegram)
                </th>
                <th scope="col" className="px-5 py-3 font-medium">
                  What it does
                </th>
                <th scope="col" className="px-5 py-3 font-medium">
                  What you get
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {rows.map((r) => (
                <tr
                  key={r.say}
                  className="align-top transition-colors hover:bg-slate-900/40"
                >
                  <td className="px-5 py-4 font-medium text-slate-100">
                    &ldquo;{r.say}&rdquo;
                    {r.voice && <VoiceTag />}
                  </td>
                  <td className="px-5 py-4 text-slate-400">{r.does}</td>
                  <td className="px-5 py-4 text-slate-300">{r.get}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
