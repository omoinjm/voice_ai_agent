"use client";

// Sign-in. Production: Cognito managed login (authorization code + PKCE via oidc-client-ts); the API gets the ID
// token. Local dev: a pretend sign-in that sends `dev:<email>[:admin]`, which only an API in AUTH_MODE=dev accepts.

import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { config } from "./config";

type AuthState = {
  ready: boolean;
  token: string | null;
  signIn: (devEmail?: string, devAdmin?: boolean) => Promise<void>;
  completeSignIn: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);
const DEV_TOKEN_KEY = "devToken";

let manager: UserManager | null = null;
function cognito(): UserManager {
  if (!manager) {
    const { region, userPoolId, clientId, domain } = config.cognito;
    manager = new UserManager({
      authority: `https://cognito-idp.${region}.amazonaws.com/${userPoolId}`,
      client_id: clientId,
      redirect_uri: `${window.location.origin}/auth/callback`,
      post_logout_redirect_uri: window.location.origin,
      response_type: "code",
      scope: "openid email profile",
      userStore: new WebStorageStateStore({ store: window.localStorage }),
      // Cognito's logout endpoint isn't in its OIDC discovery document.
      metadataSeed: { end_session_endpoint: `${domain}/logout` },
    });
  }
  return manager;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      if (config.authMode === "dev") {
        setToken(window.localStorage.getItem(DEV_TOKEN_KEY));
      } else {
        const user = await cognito().getUser();
        setToken(user && !user.expired ? (user.id_token ?? null) : null);
      }
      setReady(true);
    })();
  }, []);

  const signIn = useCallback(async (devEmail?: string, devAdmin?: boolean) => {
    if (config.authMode === "dev") {
      if (!devEmail) throw new Error("Enter an email address");
      const t = `dev:${devEmail.trim().toLowerCase()}${devAdmin ? ":admin" : ""}`;
      window.localStorage.setItem(DEV_TOKEN_KEY, t);
      setToken(t);
      return;
    }
    await cognito().signinRedirect();
  }, []);

  const completeSignIn = useCallback(async () => {
    const user = await cognito().signinRedirectCallback();
    setToken(user.id_token ?? null);
  }, []);

  const signOut = useCallback(async () => {
    setToken(null);
    if (config.authMode === "dev") {
      window.localStorage.removeItem(DEV_TOKEN_KEY);
      return;
    }
    const m = cognito();
    await m.removeUser();
    // Cognito's /logout wants client_id + logout_uri rather than the standard OIDC parameters.
    const { domain, clientId } = config.cognito;
    window.location.href = `${domain}/logout?client_id=${clientId}&logout_uri=${encodeURIComponent(window.location.origin)}`;
  }, []);

  const value = useMemo(
    () => ({ ready, token, signIn, completeSignIn, signOut }),
    [ready, token, signIn, completeSignIn, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
