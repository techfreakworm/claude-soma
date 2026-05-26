import { Skeleton } from "@/components/ui/skeleton";

// Shown instantly (App Router streaming) while the RoutinesPage server
// component awaits /api/routines. That endpoint can be slow on a cold cache
// (the cloud-routines source spawns `claude -p`), so without this the user
// would stare at a blank screen; the skeleton mirrors the real list layout.
export default function RoutinesLoading() {
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Routines</h1>
      <p className="text-sm text-slate-500">Loading schedules…</p>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40">
        <ul className="divide-y divide-slate-800">
          {Array.from({ length: 5 }).map((_, i) => (
            <li key={i} className="p-4 space-y-2">
              <div className="flex items-center gap-2">
                <Skeleton className="h-4 w-48" />
                <Skeleton className="h-3 w-12 rounded" />
                <Skeleton className="h-3 w-16" />
              </div>
              <Skeleton className="h-3 w-64" />
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
