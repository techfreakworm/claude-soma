import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL("https://soma.mayankgupta.in"),
  title: {
    default: "Claude Soma — a body for Claude Code",
    template: "%s · Claude Soma",
  },
  description:
    "Claude Soma gives Claude Code a body: a Telegram channel, voice in and out, " +
    "and a project orchestrator that spawns persistent, isolated agent teams — " +
    "Hermes-Agent's product surface in ~10% the code, on one Oracle Cloud VPS, " +
    "authed entirely by a Claude Max subscription with no API keys.",
  openGraph: {
    title: "Claude Soma — a body for Claude Code",
    description:
      "A Telegram channel, voice in/out, and a project orchestrator that spawns " +
      "persistent isolated agent teams. Built as a Claude Code plugin. No API keys.",
    url: "https://soma.mayankgupta.in",
    siteName: "Claude Soma",
    type: "website",
  },
  icons: { icon: "/favicon.ico" },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
