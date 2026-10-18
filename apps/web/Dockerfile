# syntax=docker/dockerfile:1.7
# The AfterLedger web app (Next.js standalone server). Build from the repository root:
#   docker build -f apps/web/Dockerfile -t afterledger-web .
# One image runs anywhere: the API's address is read at request time from AL_API_BASE.

FROM node:22.23.3-bookworm-slim AS build
ENV NEXT_TELEMETRY_DISABLED=1 CI=1
# A build behind a TLS-inspecting proxy can pass its CA:  --secret id=proxy_ca,src=ca.crt
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    corepack enable && corepack prepare pnpm@10.28.0 --activate
WORKDIR /repo
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/contracts/package.json packages/contracts/
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    pnpm install --frozen-lockfile --filter @afterledger/web...
COPY packages/contracts packages/contracts
COPY apps/web apps/web
RUN pnpm --filter @afterledger/web build

FROM node:22.23.3-bookworm-slim
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=8080 HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build --chown=node:node /repo/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=node:node /repo/apps/web/public ./apps/web/public
USER node
EXPOSE 8080
CMD ["node", "apps/web/server.js"]
