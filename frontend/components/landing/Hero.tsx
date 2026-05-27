"use client";
import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";

const GITHUB_URL = "https://github.com/techfreakworm/claude-soma";

function GithubMark({ className }: { className?: string }) {
  return (
    <svg
      role="img"
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
    >
      <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.51 11.51 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222 0 1.606-.014 2.898-.014 3.293 0 .322.216.694.825.576C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
    </svg>
  );
}

export function Hero() {
  return (
    <section
      id="top"
      className="relative overflow-hidden border-b border-slate-800/60"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(60%_60%_at_50%_0%,rgba(99,102,241,0.16),transparent_70%)]"
      />
      <div className="container mx-auto max-w-5xl px-5 pt-16 pb-16 sm:px-6 sm:pt-24 sm:pb-24">
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="inline-flex items-center gap-2 rounded-full border border-indigo-400/30 bg-indigo-500/10 px-3 py-1 text-xs font-medium text-indigo-200"
        >
          <span className="relative flex size-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
            <span className="relative inline-flex size-2 rounded-full bg-indigo-400" />
          </span>
          A Claude Code plugin · no Anthropic API keys
        </motion.div>

        <motion.h1
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.05 }}
          className="mt-6 text-4xl leading-[1.1] font-bold tracking-tight sm:text-5xl md:text-6xl"
        >
          A body for{" "}
          <span className="text-indigo-400">Claude Code</span>.
          <br className="hidden sm:block" /> Hermes-Agent&apos;s product surface
          in <span className="text-indigo-400">~10% the code</span>.
        </motion.h1>

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.2 }}
          className="mt-6 max-w-2xl text-base text-slate-400 sm:text-lg md:text-xl"
        >
          Claude Soma (Greek <em>soma</em>, body) gives Claude Code a Telegram
          channel, voice in and out, a project orchestrator that spawns
          persistent, independent agent teams per workstream, social posting,
          and a showcase dashboard — all riding Claude Code&apos;s native rails.
        </motion.p>

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.35 }}
          className="mt-9 flex flex-col gap-3 sm:flex-row sm:items-center"
        >
          <a
            href={GITHUB_URL}
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-indigo-500 px-5 text-sm font-semibold text-white transition-colors hover:bg-indigo-400 focus-visible:ring-2 focus-visible:ring-indigo-300 focus-visible:outline-none"
          >
            <GithubMark className="size-4" />
            View source on GitHub
          </a>
          <a
            href="#features"
            className="group inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-slate-700 bg-slate-900/40 px-5 text-sm font-semibold text-slate-200 transition-colors hover:border-slate-600 hover:bg-slate-800/60 focus-visible:ring-2 focus-visible:ring-indigo-300 focus-visible:outline-none"
          >
            Explore the features
            <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" />
          </a>
        </motion.div>

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.5 }}
          className="mt-6 font-mono text-xs text-slate-500"
        >
          One Oracle Cloud ARM free-tier VPS · authed entirely through Claude Max
        </motion.p>
      </div>
    </section>
  );
}
