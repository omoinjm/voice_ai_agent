"use client";

import { useCallback, useEffect, useState } from "react";
import { Shell, StatusBadge } from "@/components/Shell";
import { BUSINESS_CATEGORIES, STATUS_LABELS, useApi, type BusinessCategory, type Tenant } from "@/lib/api";

const CATEGORY_LABELS: Record<BusinessCategory, string> = {
  medical: "Medical practice",
  legal: "Legal services",
  insurance: "Insurance",
  financial: "Financial services",
  beauty: "Beauty and wellness",
  other: "Other",
};

function RegisterBusiness({ onCreated }: { onCreated: () => void }) {
  const api = useApi();
  const [name, setName] = useState("");
  const [category, setCategory] = useState<BusinessCategory>("medical");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <form
      className="grid gap-3 rounded-lg border border-border p-4 sm:grid-cols-[1fr_220px_auto] sm:items-end"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          await api<Tenant>("/tenants", { method: "POST", body: JSON.stringify({ name, category }) });
          setName("");
          onCreated();
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <label className="text-sm">
        Business name
        <input
          required
          minLength={2}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="mt-1 w-full rounded border border-border bg-background px-3 py-2"
        />
      </label>
      <label className="text-sm">
        Type of business
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value as BusinessCategory)}
          className="mt-1 w-full rounded border border-border bg-background px-3 py-2"
        >
          {BUSINESS_CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {CATEGORY_LABELS[c]}
            </option>
          ))}
        </select>
      </label>
      <button disabled={busy} className="rounded bg-accent px-4 py-2 font-medium text-white disabled:opacity-60">
        {busy ? "Registering…" : "Register"}
      </button>
      {error && <p className="text-sm text-danger sm:col-span-3">{error}</p>}
    </form>
  );
}

function Businesses() {
  const api = useApi();
  const [tenants, setTenants] = useState<Tenant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<Tenant[]>("/tenants")
      .then(setTenants)
      .catch((e) => setError(e.message));
  }, [api]);
  useEffect(load, [load]);

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-semibold">Your businesses</h1>
        {error && <p className="mt-4 text-danger">{error}</p>}
        {tenants?.length === 0 && <p className="mt-4 text-muted">You haven&apos;t registered a business yet.</p>}
        <ul className="mt-4 divide-y divide-border rounded-lg border border-border">
          {tenants?.map((t) => (
            <li key={t.id} className="flex flex-wrap items-center justify-between gap-2 p-4">
              <div>
                <div className="font-medium">{t.name}</div>
                <div className="text-sm text-muted">{CATEGORY_LABELS[t.category]}</div>
              </div>
              <StatusBadge status={t.status} label={STATUS_LABELS[t.status] ?? t.status} />
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h2 className="mb-3 text-lg font-medium">Register a business</h2>
        <RegisterBusiness onCreated={load} />
      </section>
    </div>
  );
}

export default function Portal() {
  return <Shell>{() => <Businesses />}</Shell>;
}
