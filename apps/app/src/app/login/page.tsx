"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { config } from "@/lib/config";

export default function Login() {
  const { ready, token, signIn } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [admin, setAdmin] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ready && token) router.replace("/app");
  }, [ready, token, router]);

  if (config.authMode === "cognito") {
    return (
      <main className="mx-auto max-w-sm px-4 py-20 text-center">
        <h1 className="text-2xl font-semibold">{config.productName}</h1>
        <button
          onClick={() => signIn().catch((e) => setError(e.message))}
          className="mt-8 w-full rounded bg-accent px-4 py-2 font-medium text-white hover:opacity-90"
        >
          Sign in or create an account
        </button>
        {error && <p className="mt-4 text-sm text-danger">{error}</p>}
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-sm px-4 py-20">
      <h1 className="text-2xl font-semibold">Sign in (local development)</h1>
      <p className="mt-2 text-sm text-muted">No password locally: the API runs in dev auth mode.</p>
      <form
        className="mt-6 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          signIn(email, admin).catch((err) => setError(err.message));
        }}
      >
        <label className="block text-sm">
          Email
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded border border-border bg-background px-3 py-2"
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={admin} onChange={(e) => setAdmin(e.target.checked)} />
          Platform admin
        </label>
        <button className="w-full rounded bg-accent px-4 py-2 font-medium text-white hover:opacity-90">Sign in</button>
        {error && <p className="text-sm text-danger">{error}</p>}
      </form>
    </main>
  );
}
