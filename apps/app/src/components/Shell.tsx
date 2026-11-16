"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useApi, type Me } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { config } from "@/lib/config";

/** Page frame for signed-in pages: redirects to /login when signed out, and to /app if a non-admin opens /admin. */
export function Shell({ admin = false, children }: { admin?: boolean; children: (me: Me) => React.ReactNode }) {
  const { ready, token, signOut } = useAuth();
  const api = useApi();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    if (!token) {
      router.replace("/login");
      return;
    }
    api<Me>("/me")
      .then((m) => {
        if (admin && !m.is_admin) router.replace("/app");
        else setMe(m);
      })
      .catch((e) => setError(e.message));
  }, [ready, token, admin, api, router]);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <Link href={admin ? "/admin" : "/app"} className="font-semibold">
            {config.productName}
            {admin && <span className="ml-2 rounded bg-accent/15 px-1.5 py-0.5 text-xs text-accent">Admin</span>}
          </Link>
          {me && (
            <div className="flex items-center gap-3 text-sm">
              {me.is_admin && (
                <Link href={admin ? "/app" : "/admin"} className="text-muted hover:text-foreground">
                  {admin ? "Business view" : "Admin console"}
                </Link>
              )}
              <span className="hidden text-muted sm:inline">{me.email}</span>
              <button onClick={() => signOut()} className="rounded border border-border px-2 py-1 hover:bg-surface">
                Sign out
              </button>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">
        {error ? (
          <p className="text-danger">Couldn&apos;t load your account: {error}</p>
        ) : me ? (
          children(me)
        ) : (
          <p className="text-muted">Loading…</p>
        )}
      </main>
    </div>
  );
}

export function StatusBadge({ status, label }: { status: string; label: string }) {
  const tone =
    status === "live"
      ? "bg-success/15 text-success"
      : status === "rejected" || status === "suspended"
        ? "bg-danger/15 text-danger"
        : status === "info_requested"
          ? "bg-warning/15 text-warning"
          : "bg-accent/15 text-accent";
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${tone}`}>{label}</span>;
}
