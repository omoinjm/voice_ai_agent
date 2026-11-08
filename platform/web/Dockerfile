# syntax=docker/dockerfile:1
# Production image (ECS). Local development uses the `web` service in platform/docker-compose.yml instead.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* values are inlined into the browser bundle at build time, so each environment gets its own build.
ARG NEXT_PUBLIC_PRODUCT_NAME NEXT_PUBLIC_API_URL NEXT_PUBLIC_AUTH_MODE=cognito NEXT_PUBLIC_COGNITO_REGION \
    NEXT_PUBLIC_COGNITO_USER_POOL_ID NEXT_PUBLIC_COGNITO_CLIENT_ID NEXT_PUBLIC_COGNITO_DOMAIN
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run lint && npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
