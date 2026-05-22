export function KpiCard({ label, value, hint }: {
  label: string; value: string | number; hint?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
      <div className="text-xs uppercase tracking-wider text-slate-500">{label}</div>
      <div className="mt-2 text-3xl font-mono font-bold text-slate-100">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}
