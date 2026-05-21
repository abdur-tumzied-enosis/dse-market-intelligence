"use client";

import { use, useEffect, useState } from "react";
import { api, StreamInfo, AdapterInfo } from "@/lib/api";

function priorityBadge(delta: number) {
  if (delta < 0) return <span className="text-green-400 text-xs">+{Math.abs(delta)} promoted</span>;
  if (delta > 0) return <span className="text-yellow-400 text-xs">−{delta} demoted</span>;
  return null;
}

function AdapterRow({ stream, adapter, onRefresh }: {
  stream: string;
  adapter: AdapterInfo;
  onRefresh: () => void;
}) {
  const [loading, setLoading] = useState(false);

  const action = async (fn: () => Promise<unknown>) => {
    setLoading(true);
    try { await fn(); onRefresh(); }
    finally { setLoading(false); }
  };

  return (
    <div className={`flex items-center justify-between border rounded px-4 py-3 ${
      adapter.paused ? "bg-gray-900 border-gray-700 opacity-60" : "bg-gray-900 border-gray-800"
    }`}>
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <span className="text-white font-medium">{adapter.name}</span>
          {adapter.paused && <span className="text-xs px-1 bg-gray-700 text-gray-400 rounded">paused</span>}
          {priorityBadge(adapter.priority_delta)}
        </div>
        <div className="text-xs text-gray-500">
          priority {adapter.effective_priority} · timeout {adapter.timeout_seconds}s
        </div>
      </div>
      <div className="flex gap-2">
        <button disabled={loading} onClick={() => action(() => api.streams.promoteAdapter(stream, adapter.name))}
          className="text-xs px-2 py-1 bg-gray-800 hover:bg-gray-700 rounded">▲</button>
        <button disabled={loading} onClick={() => action(() => api.streams.demoteAdapter(stream, adapter.name))}
          className="text-xs px-2 py-1 bg-gray-800 hover:bg-gray-700 rounded">▼</button>
        {adapter.paused ? (
          <button disabled={loading} onClick={() => action(() => api.streams.resumeAdapter(stream, adapter.name))}
            className="text-xs px-2 py-1 bg-green-900 hover:bg-green-800 text-green-300 rounded">resume</button>
        ) : (
          <button disabled={loading} onClick={() => action(() => api.streams.pauseAdapter(stream, adapter.name))}
            className="text-xs px-2 py-1 bg-red-900 hover:bg-red-800 text-red-300 rounded">pause</button>
        )}
      </div>
    </div>
  );
}

export default function StreamPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [stream, setStream] = useState<StreamInfo | null>(null);
  const [error, setError] = useState("");

  const load = () => {
    api.streams.get(id)
      .then(setStream)
      .catch((e) => setError(String(e)));
  };

  useEffect(load, [id]);

  if (error) return <div className="text-red-400">{error}</div>;
  if (!stream) return <div className="text-gray-500">Loading…</div>;

  const sorted = [...stream.adapters].sort((a, b) => a.effective_priority - b.effective_priority);

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="flex items-center justify-between">
        <div>
          <a href="/streams" className="text-gray-500 text-sm hover:text-white">← Streams</a>
          <h1 className="text-xl font-bold text-white mt-1">{stream.name}</h1>
        </div>
        <span className="text-sm text-gray-400">{stream.active_adapters}/{stream.adapter_count} active</span>
      </div>

      <div className="space-y-2">
        <h2 className="text-sm text-gray-400 uppercase">Adapters (priority order)</h2>
        {sorted.map((a) => (
          <AdapterRow key={a.name} stream={stream.name} adapter={a} onRefresh={load} />
        ))}
      </div>
    </div>
  );
}
