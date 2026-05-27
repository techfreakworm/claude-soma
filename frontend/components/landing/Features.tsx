import {
  MessageSquare,
  AudioLines,
  Workflow,
  Boxes,
  Share2,
  CalendarClock,
  LayoutDashboard,
  KeyRound,
  type LucideIcon,
} from "lucide-react";

type Feature = {
  icon: LucideIcon;
  title: string;
  body: string;
};

const features: Feature[] = [
  {
    icon: MessageSquare,
    title: "Telegram channel",
    body: "DM the bot in text or voice; it replies in text or voice. The bot is an orchestrator — anything slow is dispatched to a background subagent and acked immediately, so chat never blocks.",
  },
  {
    icon: AudioLines,
    title: "Voice in / out",
    body: "voice-stt (whisper.cpp) transcribes your voice memos; voice-tts (piper → opus) speaks the replies back as a voice note.",
  },
  {
    icon: Workflow,
    title: "Project orchestration",
    body: "Spin up a persistent project-lead per workstream. Each lead is an independent Claude Code session in its own working dir, with its own Remote Control URL, that can run its own agent team.",
  },
  {
    icon: Boxes,
    title: "cgroup-isolated leads",
    body: "Every lead runs in its own transient systemd unit and tmux server, so a channel restart can't take it down.",
  },
  {
    icon: Share2,
    title: "Social posting",
    body: "Per-platform Playwright MCP servers post to X, LinkedIn, and Medium using a shared persistent browser-auth store — log in once via VNC, reused everywhere, refreshed weekly.",
  },
  {
    icon: CalendarClock,
    title: "Scheduled routines",
    body: "Server-hosted cron via Claude's cloud routines, plus systemd timers and crontab, all surfaced together.",
  },
  {
    icon: LayoutDashboard,
    title: "Showcase dashboard",
    body: "FastAPI + Next.js admin and showcase behind Caddy, gated by GitHub OAuth, reading live Claude state over a Unix-socket bridge.",
  },
  {
    icon: KeyRound,
    title: "Max OAuth only",
    body: "Every Claude call draws on your Max plan. No Anthropic API key anywhere — ever.",
  },
];

export function Features() {
  return (
    <section
      id="features"
      className="container mx-auto max-w-6xl px-5 py-16 sm:px-6 sm:py-24 scroll-mt-20"
    >
      <div className="max-w-2xl">
        <p className="text-sm font-semibold tracking-wider text-indigo-400 uppercase">
          Features
        </p>
        <h2 className="mt-3 text-2xl font-bold tracking-tight sm:text-3xl md:text-4xl">
          Everything the platform layer used to be — native instead
        </h2>
        <p className="mt-4 text-base text-slate-400 sm:text-lg">
          Channels, agent teams, scheduled routines, mobile push, MCP, hooks,
          plugins and auto-memory are native to Claude Code. Soma is the missing
          slice on top.
        </p>
      </div>

      <ul className="mt-12 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {features.map(({ icon: Icon, title, body }) => (
          <li
            key={title}
            className="group flex flex-col rounded-xl border border-slate-800 bg-slate-900/40 p-5 transition-colors hover:border-indigo-500/40 hover:bg-slate-900/70"
          >
            <span className="grid size-10 place-items-center rounded-lg bg-indigo-500/10 text-indigo-300 ring-1 ring-indigo-400/20 transition-colors group-hover:bg-indigo-500/20">
              <Icon className="size-5" aria-hidden="true" />
            </span>
            <h3 className="mt-4 text-base font-semibold text-slate-100">
              {title}
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">
              {body}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
