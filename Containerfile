FROM node:24-bookworm-slim AS renderer
WORKDIR /build
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN corepack pnpm install --frozen-lockfile --ignore-scripts

FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=renderer /build/node_modules ./node_modules
COPY *.py trace.mjs package.json LICENSE ./
COPY public ./public
USER node
EXPOSE 8765
CMD ["python3","server.py","--bind","0.0.0.0"]
