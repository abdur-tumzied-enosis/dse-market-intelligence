import { api } from "@/lib/api";

export default async function StreamsPage() {
  const streams = await api.streams.list();
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold text-white">Streams</h1>
      <div className="space-y-2">
        {streams.map((s) => (
          <a key={s.name} href={`/streams/${s.name}`}
            className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded px-4 py-3 hover:border-gray-600">
            <span className="text-white">{s.name}</span>
            <span className="text-sm text-gray-400">{s.active_adapters}/{s.adapter_count} adapters active</span>
          </a>
        ))}
      </div>
    </div>
  );
}
