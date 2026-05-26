// mgmt-ui/app/access/UserOverridesTab.tsx
"use client";

import { useEffect, useState, useMemo } from "react";
import { UserSummary, UserOverride, api } from "@/lib/api";

const FLAG_OPTIONS = [
  "predictions",
  "reports",
  "portfolio_analysis",
  "chat",
  "screener_health_score",
];

const TIERS = ["free", "pro", "pro_plus", "institution"] as const;
type Tier = typeof TIERS[number];

const TIER_COLORS: Record<string, string> = {
  free:        "bg-gray-800 text-gray-400",
  pro:         "bg-blue-900 text-blue-300",
  pro_plus:    "bg-purple-900 text-purple-300",
  institution: "bg-yellow-900 text-yellow-300",
};

export default function UserOverridesTab() {
  const [allUsers, setAllUsers]         = useState<UserSummary[]>([]);
  const [query, setQuery]               = useState("");
  const [loading, setLoading]           = useState(true);
  const [error, setError]               = useState<string | null>(null);

  const [selectedUser, setSelectedUser] = useState<UserSummary | null>(null);
  const [overrides, setOverrides]       = useState<UserOverride[]>([]);
  const [loadingOverrides, setLoadingOverrides] = useState(false);
  const [editingTierId, setEditingTierId] = useState<number | null>(null);
  const [savingTier, setSavingTier]       = useState(false);

  // New override form
  const [newFlag, setNewFlag]         = useState(FLAG_OPTIONS[0]);
  const [newOverride, setNewOverride] = useState<"grant" | "revoke">("grant");
  const [newExpiry, setNewExpiry]     = useState("");
  const [newNote, setNewNote]         = useState("");
  const [adding, setAdding]           = useState(false);

  useEffect(() => {
    api.access.listUsers()
      .then(setAllUsers)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allUsers;
    return allUsers.filter((u) => u.email.toLowerCase().includes(q));
  }, [allUsers, query]);

  async function changeTier(user: UserSummary, tier: Tier) {
    setSavingTier(true);
    setError(null);
    try {
      const updated = await api.access.updateUser(user.id, { tier });
      setAllUsers((prev) => prev.map((u) => u.id === updated.id ? updated : u));
      if (selectedUser?.id === updated.id) setSelectedUser(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingTier(false);
      setEditingTierId(null);
    }
  }

  async function toggleActive(user: UserSummary) {
    setError(null);
    try {
      const updated = await api.access.updateUser(user.id, { is_active: !user.is_active });
      setAllUsers((prev) => prev.map((u) => u.id === updated.id ? updated : u));
      if (selectedUser?.id === updated.id) setSelectedUser(updated);
    } catch (e) {
      setError(String(e));
    }
  }

  async function selectUser(user: UserSummary) {
    setSelectedUser(user);
    setOverrides([]);
    setLoadingOverrides(true);
    try {
      const data = await api.access.userOverrides(user.id);
      setOverrides(data);
    } finally {
      setLoadingOverrides(false);
    }
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
      {/* Search / filter */}
      <div className="flex gap-2 items-center">
        <input
          type="text"
          placeholder="Filter by email…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-72 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white text-sm"
        />
        <span className="text-gray-500 text-xs">
          {loading ? "Loading…" : `${filtered.length} user${filtered.length !== 1 ? "s" : ""}`}
        </span>
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}

      {/* Users table */}
      {!loading && (
        <div className="overflow-x-auto rounded border border-gray-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 bg-gray-900/60">
                <th className="text-left px-4 py-2 text-gray-400 font-medium">Email</th>
                <th className="text-left px-4 py-2 text-gray-400 font-medium">Tier</th>
                <th className="text-left px-4 py-2 text-gray-400 font-medium">Status</th>
                <th className="text-left px-4 py-2 text-gray-400 font-medium">ID</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-gray-600 text-sm">
                    No users found
                  </td>
                </tr>
              )}
              {filtered.map((u) => (
                <tr
                  key={u.id}
                  onClick={() => selectUser(u)}
                  className={`border-b border-gray-800/50 cursor-pointer transition-colors ${
                    selectedUser?.id === u.id
                      ? "bg-green-950/60"
                      : "hover:bg-gray-800/40"
                  }`}
                >
                  <td className="px-4 py-3 text-white">{u.email}</td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    {editingTierId === u.id ? (
                      <select
                        autoFocus
                        defaultValue={u.tier}
                        disabled={savingTier}
                        onChange={(e) => changeTier(u, e.target.value as Tier)}
                        onBlur={() => setEditingTierId(null)}
                        className="bg-gray-900 border border-gray-600 rounded px-1 py-0.5 text-white text-xs disabled:opacity-50"
                      >
                        {TIERS.map((t) => (
                          <option key={t} value={t}>{t}</option>
                        ))}
                      </select>
                    ) : (
                      <button
                        onClick={() => setEditingTierId(u.id)}
                        title="Click to change tier"
                        className={`text-xs px-2 py-0.5 rounded font-medium hover:ring-1 hover:ring-white/30 transition-all ${
                          TIER_COLORS[u.tier] ?? "bg-gray-800 text-gray-400"
                        }`}
                      >
                        {u.tier}
                      </button>
                    )}
                  </td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => toggleActive(u)}
                      title={u.is_active ? "Click to deactivate" : "Click to activate"}
                      className={`text-xs hover:underline transition-colors ${
                        u.is_active ? "text-green-400 hover:text-red-400" : "text-red-400 hover:text-green-400"
                      }`}
                    >
                      {u.is_active ? "active" : "inactive"}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-gray-600 font-mono text-xs">{u.id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Overrides panel for selected user */}
      {selectedUser && (
        <div className="border border-gray-800 rounded p-4 space-y-4">
          <h2 className="text-sm text-gray-400">
            Overrides for{" "}
            <span className="text-white font-medium">{selectedUser.email}</span>
          </h2>

          {loadingOverrides && (
            <p className="text-gray-600 text-sm">Loading overrides…</p>
          )}

          {!loadingOverrides && overrides.length === 0 && (
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
                  {isExpired(o)
                    ? "expired"
                    : `until ${new Date(o.expires_at).toLocaleDateString()}`}
                </span>
              )}
              {o.note && (
                <span className="text-xs text-gray-500 italic">{o.note}</span>
              )}
              <button
                onClick={() => removeOverride(o.flag_key)}
                className="text-gray-600 hover:text-red-400 text-xs ml-2"
              >
                ✕
              </button>
            </div>
          ))}

          {/* Add override form */}
          <div className="border-t border-gray-800 pt-4 space-y-2">
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
