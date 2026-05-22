import { api } from "@/lib/api";
import { ProjectTree } from "@/components/admin/ProjectTree";

type Project = {
  name: string; agent_id: string; type: string; rc_url: string;
  status: string; idle_for_seconds: number;
};

export default async function ProjectsPage() {
  const projects = await api<Project[]>("/api/projects").catch(() => [] as Project[]);
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
