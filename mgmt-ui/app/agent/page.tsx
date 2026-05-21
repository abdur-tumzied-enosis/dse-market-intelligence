"use client";

import { useEffect, useRef, useState } from "react";
import { api, Decision, chatStream } from "@/lib/api";

const riskColor: Record<string, string> = {
  low: "text-green-400",
  medium: "text-yellow-400",
  high: "text-red-400",
  none: "text-gray-500",
};

const statusBadge: Record<string, string> = {
  auto_executed: "bg-green-900 text-green-300",
  pending_approval: "bg-yellow-900 text-yellow-300",
  approved: "bg-blue-900 text-blue-300",
  rejected: "bg-gray-800 text-gray-400",
};

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  toolCalls?: Array<{ name: string; input: unknown; result: unknown }>;
};

export default function AgentPage() {
  const [tab, setTab] = useState<"queue" | "decisions" | "chat">("queue");
  const [queue, setQueue] = useState<Decision[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [sweeping, setSweeping] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadData = () => {
    api.agent.queue().then(setQueue).catch(console.error);
    api.agent.decisions().then(setDecisions).catch(console.error);
  };

  useEffect(() => { loadData(); }, []);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const runSweep = async () => {
    setSweeping(true);
    try { await api.agent.run(); loadData(); }
    finally { setSweeping(false); }
  };

  const approve = async (id: number) => {
    await api.agent.approve(id);
    loadData();
  };

  const reject = async (id: number) => {
    await api.agent.reject(id);
    loadData();
  };

  const sendChat = () => {
    if (!input.trim() || streaming) return;

    const userMsg: ChatMessage = { role: "user", content: input.trim() };
    const assistantMsg: ChatMessage = { role: "assistant", content: "", toolCalls: [] };
    setMessages((m) => [...m, userMsg, assistantMsg]);
    setInput("");
    setStreaming(true);

    const apiMessages = [...messages, userMsg].map((m) => ({ role: m.role, content: m.content }));

    stopRef.current = chatStream(
      apiMessages,
      (chunk) => {
        if (chunk.type === "text") {
          setMessages((m) => {
            const copy = [...m];
            copy[copy.length - 1] = { ...copy[copy.length - 1], content: copy[copy.length - 1].content + (chunk.text ?? "") };
            return copy;
          });
        } else if (chunk.type === "tool_call" || chunk.type === "tool_result") {
          setMessages((m) => {
            const copy = [...m];
            const last = copy[copy.length - 1];
            const tc = [...(last.toolCalls ?? [])];
            if (chunk.type === "tool_call") tc.push({ name: chunk.name as string, input: chunk.input, result: null });
            else if (tc.length) tc[tc.length - 1] = { ...tc[tc.length - 1], result: chunk.result };
            copy[copy.length - 1] = { ...last, toolCalls: tc };
            return copy;
          });
        }
      },
      () => setStreaming(false),
      (err) => {
        setStreaming(false);
        console.error(err);
      },
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Ops Agent</h1>
        <button onClick={runSweep} disabled={sweeping}
          className="text-sm px-3 py-1 bg-indigo-800 hover:bg-indigo-700 disabled:opacity-50 rounded">
          {sweeping ? "Sweeping…" : "Run Sweep"}
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-800 text-sm">
        {(["queue", "decisions", "chat"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-2 -mb-px ${tab === t ? "border-b-2 border-indigo-500 text-white" : "text-gray-500 hover:text-gray-300"}`}>
            {t === "queue" ? `Queue (${queue.length})` : t === "decisions" ? "History" : "Chat"}
          </button>
        ))}
      </div>

      {/* Queue */}
      {tab === "queue" && (
        <div className="space-y-3">
          {queue.length === 0 && <p className="text-gray-600 text-sm">No pending approvals.</p>}
          {queue.map((d) => (
            <div key={d.id} className="bg-gray-900 border border-yellow-800 rounded p-4 space-y-2">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-white font-medium">{d.action_type}</span>
                    <span className={`text-xs ${riskColor[d.risk_level]}`}>{d.risk_level} risk</span>
                    <span className="text-xs text-gray-500">{d.run_mode}</span>
                  </div>
                  <p className="text-xs text-gray-400 font-mono">{d.target}</p>
                  <p className="text-sm text-gray-300">{d.reasoning}</p>
                </div>
                <div className="flex gap-2 shrink-0">
                  <button onClick={() => approve(d.id)}
                    className="text-xs px-2 py-1 bg-green-900 hover:bg-green-800 text-green-300 rounded">Approve</button>
                  <button onClick={() => reject(d.id)}
                    className="text-xs px-2 py-1 bg-red-900 hover:bg-red-800 text-red-300 rounded">Reject</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Decision history */}
      {tab === "decisions" && (
        <div className="space-y-2">
          {decisions.map((d) => (
            <div key={d.id} className="bg-gray-900 border border-gray-800 rounded px-4 py-3">
              <div className="flex items-center gap-3 text-sm">
                <span className="text-white">{d.action_type}</span>
                <span className={`text-xs px-1 rounded ${statusBadge[d.status] ?? "bg-gray-800 text-gray-400"}`}>{d.status}</span>
                <span className={`text-xs ${riskColor[d.risk_level]}`}>{d.risk_level}</span>
                <span className="text-xs text-gray-500 ml-auto">{new Date(d.decided_at).toLocaleString()}</span>
              </div>
              <p className="text-xs text-gray-400 mt-1 font-mono truncate">{d.target}</p>
            </div>
          ))}
          {decisions.length === 0 && <p className="text-gray-600 text-sm">No decisions yet.</p>}
        </div>
      )}

      {/* Chat */}
      {tab === "chat" && (
        <div className="flex flex-col gap-3">
          <div className="bg-gray-950 border border-gray-800 rounded h-96 overflow-y-auto p-4 space-y-4">
            {messages.length === 0 && (
              <p className="text-gray-600 text-sm">Ask the ops agent about the pipeline…</p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={m.role === "user" ? "text-right" : "text-left"}>
                <div className={`inline-block max-w-[80%] rounded px-3 py-2 text-sm whitespace-pre-wrap ${
                  m.role === "user" ? "bg-indigo-900 text-indigo-100" : "bg-gray-800 text-gray-200"
                }`}>
                  {m.content || (streaming && i === messages.length - 1 ? "▋" : "")}
                </div>
                {(m.toolCalls ?? []).length > 0 && (
                  <div className="mt-1 space-y-1">
                    {m.toolCalls!.map((tc, j) => (
                      <div key={j} className="text-xs text-gray-500 font-mono">
                        🔧 {tc.name}({JSON.stringify(tc.input).slice(0, 80)})
                        {tc.result !== null && <span className="text-gray-600"> → {JSON.stringify(tc.result).slice(0, 60)}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
          <div className="flex gap-2">
            <input value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendChat()}
              placeholder="Ask about the pipeline…"
              disabled={streaming}
              className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500 disabled:opacity-50" />
            <button onClick={sendChat} disabled={streaming || !input.trim()}
              className="px-4 py-2 bg-indigo-800 hover:bg-indigo-700 disabled:opacity-50 rounded text-sm">
              {streaming ? "…" : "Send"}
            </button>
            {streaming && stopRef.current && (
              <button onClick={() => { stopRef.current?.(); setStreaming(false); }}
                className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded text-xs text-red-400">
                Stop
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
