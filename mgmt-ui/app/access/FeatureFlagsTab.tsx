// mgmt-ui/app/access/FeatureFlagsTab.tsx
"use client";

import { useState } from "react";
import { FeatureFlag, api } from "@/lib/api";

const ALL_TIERS = ["free", "pro", "pro_plus", "institution"];
const ALL_FLAGS = [
  "predictions",
  "reports",
  "portfolio_analysis",
  "chat",
  "screener_health_score",
];

export default function FeatureFlagsTab({ initialFlags }: { initialFlags: FeatureFlag[] }) {
  const [flags, setFlags] = useState<FeatureFlag[]>(initialFlags);
  const [saving, setSaving] = useState<string | null>(null); // "flagKey:tier"
  const [error, setError] = useState<string | null>(null);

  function isEnabled(flagKey: string, tier: string): boolean {
    return flags.find((f) => f.flag_key === flagKey && f.tier === tier)?.enabled ?? false;
  }

  async function toggle(flagKey: string, tier: string) {
    const current = isEnabled(flagKey, tier);
    const key = `${flagKey}:${tier}`;
    setSaving(key);
    setError(null);
    try {
      await api.access.updateFeature(flagKey, tier, !current);
      setFlags((prev) =>
        prev.map((f) =>
          f.flag_key === flagKey && f.tier === tier ? { ...f, enabled: !current } : f
        )
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="overflow-x-auto">
      <p className="text-xs text-gray-500 mb-3">
        Changes take effect within 60 seconds (Redis TTL).
      </p>
      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800">
            <th className="text-left py-2 pr-6 text-gray-400 font-medium">Feature</th>
            {ALL_TIERS.map((t) => (
              <th key={t} className="text-left py-2 pr-6 text-gray-400 font-medium capitalize">
                {t.replace(/_/g, " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ALL_FLAGS.map((flagKey) => (
            <tr key={flagKey} className="border-b border-gray-900">
              <td className="py-3 pr-6 text-gray-300 font-mono text-xs">{flagKey}</td>
              {ALL_TIERS.map((tier) => {
                const enabled = isEnabled(flagKey, tier);
                const key = `${flagKey}:${tier}`;
                const isSaving = saving === key;
                return (
                  <td key={tier} className="py-3 pr-6">
                    <button
                      onClick={() => toggle(flagKey, tier)}
                      disabled={isSaving}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors disabled:opacity-50 ${
                        enabled ? "bg-green-600" : "bg-gray-700"
                      }`}
                      title={enabled ? "Enabled — click to disable" : "Disabled — click to enable"}
                    >
                      <span
                        className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                          enabled ? "translate-x-5" : "translate-x-1"
                        }`}
                      />
                    </button>
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
