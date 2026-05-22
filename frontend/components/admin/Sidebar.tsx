import Link from "next/link";

const NAV = [
  { href: "/admin", label: "Overview" },
  { href: "/admin/projects", label: "Projects" },
  { href: "/admin/conversations", label: "Conversations" },
  { href: "/admin/routines", label: "Routines" },
  { href: "/admin/usage", label: "Usage" },
  { href: "/admin/memory", label: "Memory" },
  { href: "/admin/logs", label: "Logs" },
];

export function Sidebar() {
  return (
    <aside className="w-56 shrink-0 border-r border-slate-800 bg-slate-900/30 px-4 py-6 sticky top-0 h-screen">
      <div className="text-sm font-mono text-slate-400 mb-6">claude-soma/admin</div>
      <nav className="space-y-1">
        {NAV.map((item) => (
          <Link key={item.href} href={item.href}
                className="block px-3 py-2 rounded text-sm hover:bg-slate-800/60 text-slate-300">
            {item.label}
          </Link>
        ))}
      </nav>
    </aside>
  );
}
