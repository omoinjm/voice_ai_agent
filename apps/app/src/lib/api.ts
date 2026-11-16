"use client";

import { useCallback } from "react";
import { useAuth } from "./auth";
import { config } from "./config";

export type Me = { id: string; email: string; name: string | null; is_admin: boolean };

export const BUSINESS_CATEGORIES = ["medical", "legal", "insurance", "financial", "beauty", "other"] as const;
export type BusinessCategory = (typeof BUSINESS_CATEGORIES)[number];

export type Tenant = {
  id: string;
  slug: string;
  name: string;
  category: BusinessCategory;
  status: string;
  created_at: string;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** fetch() against the platform API with the signed-in user's token; throws ApiError with the API's detail. */
export function useApi() {
  const { token } = useAuth();
  return useCallback(
    async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      const res = await fetch(`${config.apiUrl}${path}`, {
        ...init,
        headers: {
          "content-type": "application/json",
          ...(token ? { authorization: `Bearer ${token}` } : {}),
          ...init.headers,
        },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        const detail = body?.detail;
        throw new ApiError(
          res.status,
          typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : res.statusText,
        );
      }
      return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
    },
    [token],
  );
}

export const STATUS_LABELS: Record<string, string> = {
  registered: "Application not submitted",
  in_review: "In review",
  info_requested: "More information needed",
  rejected: "Rejected",
  whatsapp_pending: "Connect WhatsApp",
  agent_setup: "Set up your agent",
  provisioning: "Setting up",
  staging_ready: "Testing",
  live: "Live",
  suspended: "Suspended",
};
