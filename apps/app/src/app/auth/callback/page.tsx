"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";

/** Cognito managed login redirects here with ?code=…; exchange it for tokens, then go to the portal. */
export default function AuthCallback() {
  const { completeSignIn } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // the code can only be exchanged once (React dev mode runs effects twice)
    started.current = true;
    completeSignIn()
      .then(() => router.replace("/app"))
      .catch((e) => setError(e.message));
  }, [completeSignIn, router]);

  return (
    <main className="mx-auto max-w-sm px-4 py-20 text-center">
      {error ? <p className="text-danger">Sign-in failed: {error}</p> : <p className="text-muted">Signing you in…</p>}
    </main>
  );
}
