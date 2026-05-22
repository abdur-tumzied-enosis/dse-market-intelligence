"use client";

import { useEffect, useState, useCallback } from "react";
import { api, TaskInfo, TaskRun, TaskStatus } from "@/lib/api";

const queueColor: Record<string, string> = {
  scraper: "text-blue-400",
  nlp: "text-purple-400",
  ml: "text-orange-400",
};

const statusColor: Record<string, string> = {
  PENDING: "text-gray-400",
  STARTED: "text-yellow-400",
  SUCCESS: "text-green-400",
  FAILURE: "text-red-400",
  RETRY: "text-orange-400",
  REVOKED: "text-gray-500",
  queued: "text-yellow-400",
};

type RunRecord = TaskRun & { polledStatus?: TaskStatus };

export default function TasksPage() {
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.tasks.list().then(setTasks).catch((e) => setError(String(e)));
  }, []);

  const pollStatus = useCallback(async (taskId: string) => {
    try {
      const s = await api.tasks.status(taskId);
      setRuns((prev) =>
        prev.map((r) => (r.task_id === taskId ? { ...r, polledStatus: s } : r))
      );
      if (s.status === "PENDING" || s.status === "STARTED") {
        setTimeout(() => pollStatus(taskId), 3000);
      }
    } catch {}
  }, []);

  const runTask = async (name: string) => {
    setRunning((s) => new Set(s).add(name));
    setError(null);
    try {
      const run = await api.tasks.run(name);
      setRuns((prev) => [run, ...prev]);
      setTimeout(() => pollStatus(run.task_id), 2000);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning((s) => {
        const next = new Set(s);
        next.delete(name);
        return next;
      });
    }
  };

  return (
    <div className="max-w-4xl">
      <h1 className="text-xl font-bold mb-6">Celery Tasks</h1>

      {error && (
        <div className="mb-4 p-3 bg-red-900/40 border border-red-700 rounded text-red-300 text-sm">
          {error}
        </div>
      )}

      <div className="space-y-3 mb-10">
        {tasks.map((t) => (
          <div
            key={t.name}
            className="flex items-start justify-between gap-4 p-4 bg-gray-900 border border-gray-800 rounded"
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-mono text-sm text-white">{t.name}</span>
                <span className={`text-xs ${queueColor[t.queue] ?? "text-gray-400"}`}>
                  [{t.queue}]
                </span>
                <span className="text-xs text-gray-500">{t.estimated_duration}</span>
              </div>
              <p className="text-xs text-gray-400">{t.description}</p>
            </div>
            <button
              onClick={() => runTask(t.name)}
              disabled={running.has(t.name)}
              className="shrink-0 px-3 py-1 text-xs bg-blue-700 hover:bg-blue-600 disabled:bg-gray-700 disabled:text-gray-500 rounded transition-colors"
            >
              {running.has(t.name) ? "Queuing…" : "Run"}
            </button>
          </div>
        ))}
        {tasks.length === 0 && !error && (
          <p className="text-gray-500 text-sm">Loading tasks…</p>
        )}
      </div>

      {runs.length > 0 && (
        <>
          <h2 className="text-sm font-semibold text-gray-400 mb-3 uppercase tracking-wide">
            Recent Dispatches
          </h2>
          <div className="space-y-2">
            {runs.map((r) => {
              const s = r.polledStatus;
              const displayStatus = s?.status ?? r.status;
              return (
                <div
                  key={r.task_id}
                  className="p-3 bg-gray-900 border border-gray-800 rounded text-xs font-mono"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-white">{r.task_name}</span>
                    <span className={statusColor[displayStatus] ?? "text-gray-400"}>
                      {displayStatus}
                    </span>
                  </div>
                  <div className="text-gray-500 mt-1">{r.task_id}</div>
                  {s?.result && (
                    <pre className="mt-2 text-green-400 whitespace-pre-wrap break-all">
                      {JSON.stringify(s.result, null, 2)}
                    </pre>
                  )}
                  {s?.traceback && (
                    <pre className="mt-2 text-red-400 whitespace-pre-wrap break-all text-xs">
                      {s.traceback}
                    </pre>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
