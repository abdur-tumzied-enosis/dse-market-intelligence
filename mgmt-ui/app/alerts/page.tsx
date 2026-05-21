"use client";

import { useEffect, useState } from "react";
import { api, Alert } from "@/lib/api";

const severityColor: Record<string, string> = {
  CRITICAL: "bg-red-900 border-red-700 text-red-300",
  WARNING: "bg-yellow-900 border-yellow-700 text-yellow-300",
  INFO: "bg-gray-800 border-gray-700 text-gray-300",
};

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [unackedOnly, setUnackedOnly] = useState(false);

  const load = () =>
    api.alerts.list({ unacked_only: unackedOnly }).then(setAlerts).catch(console.error);

  useEffect(() => { load(); }, [unackedOnly]);

  const ack = async (id: number) => {
    await api.alerts.acknowledge(id);
    load();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Alerts</h1>
        <label className="flex items-center gap-2 text-sm text-gray-400">
          <input type="checkbox" checked={unackedOnly} onChange={(e) => setUnackedOnly(e.target.checked)} />
          Unacknowledged only
        </label>
      </div>

      <div className="space-y-2">
        {alerts.map((a) => (
          <div key={a.id} className={`border rounded px-4 py-3 ${severityColor[a.severity] ?? severityColor.INFO}`}>
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-1 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-xs">{a.severity}</span>
                  {a.stream_name && <span className="text-xs opacity-70">{a.stream_name}</span>}
                  <span className="text-xs opacity-50">{new Date(a.created_at).toLocaleString()}</span>
                </div>
                <p className="text-sm">{a.message}</p>
              </div>
              {!a.acknowledged_at && (
                <button onClick={() => ack(a.id)}
                  className="text-xs px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded shrink-0">
                  Ack
                </button>
              )}
              {a.acknowledged_at && (
                <span className="text-xs opacity-40 shrink-0">acked</span>
              )}
            </div>
          </div>
        ))}
        {alerts.length === 0 && <p className="text-gray-600 text-sm">No alerts.</p>}
      </div>
    </div>
  );
}
