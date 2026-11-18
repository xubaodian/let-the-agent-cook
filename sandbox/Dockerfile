FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    HOME=/home/cook \
    LANG=C.UTF-8

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && python -m pip install --no-cache-dir playwright==1.62.0 \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 cook \
    && useradd --uid 10001 --gid cook --create-home cook \
    && install -d -o cook -g cook /workspace

COPY agent/src/agent_cook/__init__.py /usr/local/lib/python3.12/site-packages/agent_cook/__init__.py
COPY agent/src/agent_cook/sandbox/__init__.py agent/src/agent_cook/sandbox/limits.py agent/src/agent_cook/sandbox/paths.py /usr/local/lib/python3.12/site-packages/agent_cook/sandbox/
COPY agent/src/agent_cook/sandbox/worker/*.py /usr/local/lib/python3.12/site-packages/agent_cook/sandbox/worker/

ENV PATH=/home/cook/.local/bin:$PATH
USER cook
WORKDIR /workspace
CMD ["sleep", "infinity"]
