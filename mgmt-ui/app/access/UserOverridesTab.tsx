// mgmt-ui/app/access/UserOverridesTab.tsx
"use client";

import { useState } from "react";
import { UserSummary, UserOverride, api } from "@/lib/api";

const FLAG_OPTIONS = [
  "predictions",
  "reports",
  "portfolio_analysis",
  "chat",
  "screener_health_score",
];

export default function UserOverridesTab() {
  const [query, setQuery] = useState("");
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [selectedUser, setSelectedUser] = useState<UserSummary | null>(null);
  const [overrides, setOverrides] = useState<UserOverride[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // New override form
  const [newFlag, setNewFlag] = useState(FLAG_OPTIONS[0]);
  const [newOverride, setNewOverride] = useState<"grant" | "revoke">("grant");
  const [newExpiry, setNewExpiry] = useState("");
  const [newNote, setNewNote] = useState("");
  const [adding, setAdding] = useState(false);

  async function search() {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    try {
      const results = await api.access.searchUsers(query);
      setUsers(results);
    } catch (e) {
      setError(String(e));
    } finally {
      setSearching(false);
    }
  }

  async function selectUser(user: UserSummary) {
    setSelectedUser(user);
    setError(null);
    const data = await api.access.userOverrides(user.id);
    setOverrides(data);
  }

  async function addOverride() {
    if (!selectedUser) return;
    setAdding(true);
    setError(null);
    try {
      const body: { flag_key: string; override: string; expires_at?: string; note?: string } = {
        flag_key: newFlag,
        override: newOverride,
        note: newNote || undefined,
      };
      if (newExpiry) body.expires_at = new Date(newExpiry).toISOString();
      const created = await api.access.createOverride(selectedUser.id, body);
      setOverrides((prev) => {
        const filtered = prev.filter((o) => o.flag_key !== created.flag_key);
        return [...filtered, created];
      });
      setNewNote("");
      setNewExpiry("");
    } catch (e) {
      setError(String(e));
    } finally {
      setAdding(false);
    }
  }

  async function removeOverride(flagKey: string) {
    if (!selectedUser) return;
    await api.access.deleteOverride(selectedUser.id, flagKey);
    setOverrides((prev) => prev.filter((o) => o.flag_key !== flagKey));
  }

  function isExpired(o: UserOverride): boolean {
    return !!o.expires_at && new Date(o.expires_at) < new Date();
  }

  return (
    <div className="space-y-6">
      {/* Search */}
      <div className="flex gap-2">
        <input
          type="text"
          placeholder="Search by email…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && search()}
          className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white text-sm"
        />
        <button
          onClick={search}
          disabled={searching}
          className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white text-sm rounded disabled:opacity-50"
        >
          {searching ? "…" : "Search"}
        </button>
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}

      {/* User list */}
      {users.length > 0 && (
        <div className="space-y-1">
          {users.map((u) => (
            <button
              key={u.id}
              onClick={() => selectUser(u)}
              className={`w-full text-left flex items-center gap-3 px-4 py-2 rounded border text-sm transition-colors ${
                selectedUser?.id === u.id
                  ? "border-green-600 bg-green-950 text-white"
                  : "border-gray-800 bg-gray-900 text-gray-300 hover:border-gray-600"
              }`}
            >
              <span className="flex-1">{u.email}</span>
              <span
                className={`text-xs px-2 py-0.5 rounded ${
                  u.tier === "pro" ? "bg-blue-900 text-blue-300" : "bg-gray-800 text-gray-400"
                }`}
              >
                {u.tier}
              </span>
              {!u.is_active && <span className="text-xs text-red-400">inactive</span>}
            </button>
          ))}
        </div>
      )}

      {/* Overrides for selected user */}
      {selectedUser && (
        <div className="space-y-4">
          <h2 className="text-sm text-gray-400">
            Overrides for <span className="text-white">{selectedUser.email}</span>
          </h2>

          {overrides.length === 0 && (
            <p className="text-gray-600 text-sm">No overrides. Tier defaults apply.</p>
          )}

          {overrides.map((o) => (
            <div
              key={o.flag_key}
              className={`flex items-center gap-3 px-4 py-2 rounded border text-sm ${
                isExpired(o)
                  ? "border-gray-800 bg-gray-950 opacity-50"
                  : o.override === "grant"
                  ? "border-green-800 bg-green-950"
                  : "border-red-900 bg-red-950"
              }`}
            >
              <span className="flex-1 text-white font-mono">{o.flag_key}</span>
              <span
                className={`text-xs font-bold uppercase ${
                  o.override === "grant" ? "text-green-400" : "text-red-400"
                }`}
              >
                {o.override}
              </span>
              {o.expires_at && (
                <span className="text-xs text-gray-500">
                  {isExpired(o) ? "expired" : `until ${new Date(o.expires_at).toLocaleDateString()}`}
                </span>
              )}
              {o.note && <span className="text-xs text-gray-500 italic">{o.note}</span>}
              <button
                onClick={() => removeOverride(o.flag_key)}
                className="text-gray-600 hover:text-red-400 text-xs ml-2"
              >
                ✕
              </button>
            </div>
          ))}

          {/* Add override form */}
          <div className="border border-gray-800 rounded p-4 space-y-3">
            <p className="text-xs text-gray-400 font-medium">Add Override</p>
            <div className="flex gap-2 flex-wrap">
              <select
                value={newFlag}
                onChange={(e) => setNewFlag(e.target.value)}
                className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs"
              >
                {FLAG_OPTIONS.map((f) => (
                  <option key={f} value={f}>{f}</option>
                ))}
              </select>
              <select
                value={newOverride}
                onChange={(e) => setNewOverride(e.target.value as "grant" | "revoke")}
                className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs"
              >
                <option value="grant">Grant</option>
                <option value="revoke">Revoke</option>
              </select>
              <input
                type="date"
                value={newExpiry}
                onChange={(e) => setNewExpiry(e.target.value)}
                className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs"
              />
              <input
                type="text"
                value={newNote}
                onChange={(e) => setNewNote(e.target.value)}
                placeholder="Note (optional)"
                className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs flex-1 min-w-32"
              />
              <button
                onClick={addOverride}
                disabled={adding}
                className="px-3 py-1 bg-green-800 hover:bg-green-700 text-white text-xs rounded disabled:opacity-50"
              >
                {adding ? "…" : "Add"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
