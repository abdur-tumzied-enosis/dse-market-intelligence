// mgmt-ui/app/access/TierLimitsTab.tsx
"use client";

import { useState } from "react";
import { TierLimit, api } from "@/lib/api";

const ALL_TIERS = ["free", "pro", "pro_plus", "institution"];
const ALL_KEYS  = ["api_calls_per_day", "chat_queries_per_day", "portfolio_holdings_max"];

function displayValue(v: number): string {
  return v === 0 ? "Unlimited" : String(v);
}

export default function TierLimitsTab({ initialLimits }: { initialLimits: TierLimit[] }) {
  const [limits, setLimits] = useState<TierLimit[]>(initialLimits);
  const [editing, setEditing] = useState<string | null>(null); // "tier:key"
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function getLimit(tier: string, key: string): number {
    return limits.find((l) => l.tier === tier && l.limit_key === key)?.limit_value ?? 0;
  }

  function startEdit(tier: string, key: string) {
    const current = getLimit(tier, key);
    setEditing(`${tier}:${key}`);
    setEditValue(current === 0 ? "0" : String(current));
    setError(null);
  }

  async function saveEdit(tier: string, key: string) {
    const parsed = parseInt(editValue, 10);
    if (isNaN(parsed) || parsed < 0) {
      setError("Must be a non-negative integer (0 = unlimited)");
      return;
    }
    setSaving(true);
    try {
      await api.access.updateTierLimit(tier, key, parsed);
      setLimits((prev) =>
        prev.map((l) =>
          l.tier === tier && l.limit_key === key ? { ...l, limit_value: parsed } : l
        )
      );
      setEditing(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="overflow-x-auto">
      <p className="text-xs text-gray-500 mb-3">
        0 = unlimited. Changes take effect within 60 seconds (Redis TTL).
      </p>
      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800">
            <th className="text-left py-2 pr-6 text-gray-400 font-medium">Limit</th>
            {ALL_TIERS.map((t) => (
              <th key={t} className="text-left py-2 pr-6 text-gray-400 font-medium capitalize">
                {t.replace(/_/g, " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ALL_KEYS.map((key) => (
            <tr key={key} className="border-b border-gray-900">
              <td className="py-3 pr-6 text-gray-300 font-mono text-xs">{key}</td>
              {ALL_TIERS.map((tier) => {
                const editKey = `${tier}:${key}`;
                const isEditing = editing === editKey;
                const value = getLimit(tier, key);
                return (
                  <td key={tier} className="py-3 pr-6">
                    {isEditing ? (
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min={0}
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          className="w-20 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white text-xs"
                          autoFocus
                        />
                        <button
                          onClick={() => saveEdit(tier, key)}
                          disabled={saving}
                          className="text-green-400 hover:text-green-300 text-xs disabled:opacity-50"
                        >
                          {saving ? "…" : "Save"}
                        </button>
                        <button
                          onClick={() => setEditing(null)}
                          className="text-gray-500 hover:text-gray-300 text-xs"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => startEdit(tier, key)}
                        className="text-white hover:text-green-400 font-mono"
                        title="Click to edit"
                      >
                        {displayValue(value)}
                      </button>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
