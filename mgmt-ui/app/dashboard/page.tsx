import { api, FreshnessEntry, SourceHealth, AlertSummary, StreamInfo } from "@/lib/api";

function freshnessColor(entry: FreshnessEntry): string {
  if (entry.stale) return "bg-gray-700 border-gray-600";
  const age = entry.last_success_at
    ? (Date.now() - new Date(entry.last_success_at).getTime()) / 3_600_000
    : Infinity;
  if (age < 1) return "bg-green-900 border-green-700";
  if (age < 6) return "bg-yellow-900 border-yellow-700";
  return "bg-red-900 border-red-700";
}

function healthDot(h: SourceHealth) {
  return h.reachable
    ? <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
    : <span className="w-2 h-2 rounded-full bg-red-500 inline-block" />;
}

function fmtAge(iso: string | null): string {
  if (!iso) return "never";
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default async function DashboardPage() {
  let streams: StreamInfo[] = [];
  let freshness: FreshnessEntry[] = [];
  let health: SourceHealth[] = [];
  let alertSummary: AlertSummary = { critical_unacked: 0, warning_unacked: 0, total_unacked: 0, last_24h: 0 };

  try {
    [streams, freshness, health, alertSummary] = await Promise.all([
      api.streams.list(),
      api.quality.freshness(),
      api.health.summary(),
      api.alerts.summary(),
    ]);
  } catch (e) {
    return (
      <div className="text-red-400">
        Failed to load — is the management API running on port 8001?
        <pre className="text-xs mt-2">{String(e)}</pre>
      </div>
    );
  }

  const freshnessMap = Object.fromEntries(freshness.map((f) => [f.stream, f]));

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Pipeline Dashboard</h1>
        <div className="flex gap-4 text-sm">
          {alertSummary.critical_unacked > 0 && (
            <span className="px-2 py-1 bg-red-900 text-red-300 rounded">
              {alertSummary.critical_unacked} CRITICAL
            </span>
          )}
          {alertSummary.warning_unacked > 0 && (
            <span className="px-2 py-1 bg-yellow-900 text-yellow-300 rounded">
              {alertSummary.warning_unacked} WARNING
            </span>
          )}
          {alertSummary.total_unacked === 0 && (
            <span className="px-2 py-1 bg-green-900 text-green-300 rounded">All clear</span>
          )}
        </div>
      </div>

      {/* Source health */}
      <section>
        <h2 className="text-sm text-gray-400 uppercase mb-3">Source Health</h2>
        <div className="flex flex-wrap gap-3">
          {health.map((h) => (
            <div key={h.source_name} className="flex items-center gap-2 bg-gray-900 border border-gray-800 rounded px-3 py-2 text-sm">
              {healthDot(h)}
              <span className="text-gray-200">{h.source_name}</span>
              {h.response_ms && <span className="text-gray-500">{h.response_ms}ms</span>}
              {h.hash_changed && <span className="text-yellow-400 text-xs">hash changed</span>}
            </div>
          ))}
          {health.length === 0 && <p className="text-gray-600 text-sm">No health checks yet.</p>}
        </div>
      </section>

      {/* Stream health grid */}
      <section>
        <h2 className="text-sm text-gray-400 uppercase mb-3">Stream Freshness</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {streams.map((s) => {
            const f = freshnessMap[s.name];
            const entry: FreshnessEntry = f ?? { stream: s.name, last_success_at: null, adapter_used: null, records_inserted: null, stale: true };
            return (
              <a key={s.name} href={`/streams/${s.name}`}
                className={`border rounded p-3 text-sm hover:opacity-80 transition-opacity ${freshnessColor(entry)}`}>
                <div className="font-medium text-white truncate">{s.name}</div>
                <div className="text-xs text-gray-300 mt-1">{fmtAge(entry.last_success_at)}</div>
                <div className="text-xs text-gray-400">
                  {s.active_adapters}/{s.adapter_count} adapters
                </div>
              </a>
            );
          })}
        </div>
      </section>

      {/* Legend */}
      <div className="flex gap-4 text-xs text-gray-500">
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded bg-green-900 border border-green-700" /> &lt;1h</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded bg-yellow-900 border border-yellow-700" /> 1–6h</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded bg-red-900 border border-red-700" /> &gt;6h</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded bg-gray-700 border border-gray-600" /> never</span>
      </div>
    </div>
  );
}
