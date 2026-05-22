"use client";
import { motion } from "framer-motion";

export function Hero() {
  return (
    <section className="container mx-auto px-6 pt-24 pb-16 max-w-5xl">
      <motion.h1
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6 }}
        className="text-4xl md:text-6xl font-bold tracking-tight leading-tight"
      >
        Hermes-Agent&apos;s value{" "}
        <span className="text-indigo-400">in 10% the code</span>,
        <br />
        by riding Claude Code&apos;s native rails.
      </motion.h1>
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.6, delay: 0.2 }}
        className="mt-6 text-lg md:text-xl text-slate-400 max-w-3xl"
      >
        A messaging gateway, voice in/out, persistent project-leads with their own agent teams,
        all reachable from a phone — built as a Claude Code plugin instead of a 27,000-LOC platform.
      </motion.p>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.6, delay: 0.4 }}
        className="mt-10 flex gap-4 text-sm"
      >
        <a href="#thesis" className="text-indigo-300 underline underline-offset-4">
          Read the build log
        </a>
        <span className="text-slate-600">·</span>
        <a href="https://github.com/techfreakworm/claude-soma"
           className="text-indigo-300 underline underline-offset-4">
          Source on GitHub
        </a>
      </motion.div>
    </section>
  );
}
