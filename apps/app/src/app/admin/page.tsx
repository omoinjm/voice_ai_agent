"use client";

import { useEffect, useState } from "react";
import { Shell, StatusBadge } from "@/components/Shell";
import { STATUS_LABELS, useApi, type Tenant } from "@/lib/api";

// Pipeline order of the onboarding journey, for the summary counts.
const PIPELINE = [
  "registered",
  "in_review",
  "info_requested",
  "whatsapp_pending",
  "agent_setup",
  "provisioning",
  "staging_ready",
  "live",
  "suspended",
  "rejected",
];

function Tenants() {
  const api = useApi();
  const [tenants, setTenants] = useState<Tenant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<Tenant[]>("/tenants")
      .then(setTenants)
      .catch((e) => setError(e.message));
  }, [api]);

  const counts = Object.fromEntries(PIPELINE.map((s) => [s, tenants?.filter((t) => t.status === s).length ?? 0]));

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Businesses</h1>
      {error && <p className="text-danger">{error}</p>}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {PIPELINE.map((s) => (
          <div key={s} className="rounded-lg border border-border p-3">
            <div className="text-xs text-muted">{STATUS_LABELS[s]}</div>
            <div className="mt-1 text-xl font-semibold tabular-nums">{counts[s]}</div>
          </div>
        ))}
      </div>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-left text-sm">
          <thead className="bg-surface text-muted">
            <tr>
              <th className="px-4 py-2 font-medium">Business</th>
              <th className="px-4 py-2 font-medium">Type</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Registered</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {tenants?.map((t) => (
              <tr key={t.id}>
                <td className="px-4 py-2">
                  <div className="font-medium">{t.name}</div>
                  <div className="font-mono text-xs text-muted">{t.slug}</div>
                </td>
                <td className="px-4 py-2 capitalize">{t.category}</td>
                <td className="px-4 py-2">
                  <StatusBadge status={t.status} label={STATUS_LABELS[t.status] ?? t.status} />
                </td>
                <td className="px-4 py-2 tabular-nums">{new Date(t.created_at).toLocaleDateString("en-ZA")}</td>
              </tr>
            ))}
            {tenants?.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-muted">
                  No businesses have registered yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function Admin() {
  return <Shell admin>{() => <Tenants />}</Shell>;
}
