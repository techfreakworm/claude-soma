import { api } from "@/lib/api";
import { ProjectTree } from "@/components/admin/ProjectTree";

type TeamMember = { handle: string; role: string; status: string };
type Project = {
  name: string; agent_id: string; type: string; rc_url: string;
  status: string; idle_for_seconds: number; team?: TeamMember[];
};

export default async function ProjectsPage() {
  const projects = await api<Project[]>("/api/projects").catch(() => [] as Project[]);
  // Each lead's agent-team roster lives behind /api/projects/<name>/team; fetch
  // them in parallel and attach so the graph can render teammates as a 3rd tier.
  await Promise.all(
    projects.map(async (p) => {
      const t = await api<{ team: TeamMember[] }>(
        `/api/projects/${encodeURIComponent(p.name)}/team`,
      ).catch(() => ({ team: [] as TeamMember[] }));
      p.team = t.team;
    }),
  );
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Projects</h1>
      {projects.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-700 p-8 text-slate-500 text-sm">
          No active project-leads. Ask the bot to spawn one from Telegram.
        </div>
      ) : (
        <ProjectTree projects={projects} />
      )}
    </div>
  );
}
