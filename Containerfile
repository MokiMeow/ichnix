FROM node:24-trixie-slim@sha256:af02d57b57719b9657a45199a79264a9437940cd55b3c7b3fc99615654f124a0 AS renderer
WORKDIR /build
COPY package.json pnpm-lock.yaml ./
RUN corepack pnpm install --frozen-lockfile --ignore-scripts

FROM ghcr.io/hydra-db/hydradb@sha256:ab71d651fefee13ebb23eb56d72316065bdca563ff7587442e07be663f2bf880 AS hydra

FROM python:3.12-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS workspace
WORKDIR /app
COPY --from=hydra /usr/local/bin/graph-node /usr/local/bin/
COPY --from=hydra /usr/lib/x86_64-linux-gnu/libgraphblas.so.7.4.0 /opt/hydra/libgraphblas.so.7
COPY --from=hydra /usr/lib/x86_64-linux-gnu/libcypher-parser.so.8.2.2 /opt/hydra/libcypher-parser.so.8
COPY --from=hydra /usr/lib/x86_64-linux-gnu/libgomp.so.1.0.0 /opt/hydra/libgomp.so.1
COPY licenses/ /usr/share/doc/ichnix/
COPY --from=hydra /usr/share/doc/libgraphblas7/copyright /usr/share/doc/ichnix/libgraphblas7.copyright
COPY --from=hydra /usr/share/doc/libcypher-parser8/copyright /usr/share/doc/ichnix/libcypher-parser8.copyright
COPY --from=hydra /usr/share/doc/gcc-14-base/copyright /usr/share/doc/ichnix/libgomp1.copyright
COPY --from=renderer /usr/local/LICENSE /usr/share/doc/ichnix/Node-LICENSE
COPY --from=renderer /usr/local/bin/node /usr/local/bin/
COPY --from=renderer /build/node_modules ./node_modules
COPY cli.py server.py demo.py mcp_server.py package.json LICENSE ./
COPY ichnix/ ./ichnix/
COPY scripts/ ./scripts/
COPY examples/ ./examples/
COPY testing/ ./testing/
COPY public/ ./public/
RUN install -d -m 0700 -o 10001 -g 10001 /var/lib/ichnix /run/ichnix-auth
ENV ICHNIX_STATE_DIR=/var/lib/ichnix LD_LIBRARY_PATH=/opt/hydra PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8765
CMD ["scripts/console.sh", "--bind", "0.0.0.0"]

FROM workspace AS test
COPY tests/ ./tests/
CMD ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM workspace AS app
