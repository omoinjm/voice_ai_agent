// Browser-visible settings (inlined at build time).
export const config = {
  productName: process.env.NEXT_PUBLIC_PRODUCT_NAME ?? "Voice Agent Platform",
  apiUrl: (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8100").replace(/\/$/, ""),
  // "cognito" in every deployed environment; "dev" only for local docker compose (matches the API's AUTH_MODE).
  authMode: (process.env.NEXT_PUBLIC_AUTH_MODE ?? "cognito") as "cognito" | "dev",
  cognito: {
    region: process.env.NEXT_PUBLIC_COGNITO_REGION ?? "us-east-1",
    userPoolId: process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID ?? "",
    clientId: process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID ?? "",
    // Managed login domain, e.g. https://auth.example.com or https://<prefix>.auth.us-east-1.amazoncognito.com
    domain: (process.env.NEXT_PUBLIC_COGNITO_DOMAIN ?? "").replace(/\/$/, ""),
  },
};
