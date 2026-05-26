// mgmt-ui/app/access/page.tsx
import { Suspense } from "react";
import TierLimitsTab from "./TierLimitsTab";
import FeatureFlagsTab from "./FeatureFlagsTab";
import UserOverridesTab from "./UserOverridesTab";
import { api } from "@/lib/api";

export default async function AccessPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string }>;
}) {
  const { tab = "limits" } = await searchParams;

  const [limits, flags] = await Promise.all([
    api.access.tierLimits(),
    api.access.features(),
  ]);

  const tabs = [
    { key: "limits", label: "Tier Limits" },
    { key: "flags",  label: "Feature Flags" },
    { key: "users",  label: "User Overrides" },
  ];

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-white">Access Control</h1>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-800">
        {tabs.map((t) => (
          <a
            key={t.key}
            href={`/access?tab=${t.key}`}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t.key
                ? "border-green-400 text-green-400"
                : "border-transparent text-gray-400 hover:text-white"
            }`}
          >
            {t.label}
          </a>
        ))}
      </div>

      {/* Tab content */}
      <Suspense fallback={<p className="text-gray-500 text-sm">Loading…</p>}>
        {tab === "limits" && <TierLimitsTab initialLimits={limits} />}
        {tab === "flags"  && <FeatureFlagsTab initialFlags={flags} />}
        {tab === "users"  && <UserOverridesTab />}
      </Suspense>
    </div>
  );
}
