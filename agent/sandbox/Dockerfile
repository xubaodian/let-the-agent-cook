FROM node:24.22.3-bookworm-slim AS node
FROM python:3.12-slim-bookworm

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    HOME=/home/cook \
    LANG=C.UTF-8 \
    MPLBACKEND=Agg

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
COPY src/agent_cook/data/native/workspace-requirements.txt /tmp/workspace-requirements.txt
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && python -m pip install --no-cache-dir --only-binary=:all: -r /tmp/workspace-requirements.txt \
    && python -m pip check \
    && python -m playwright install --with-deps chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-impress-nogui fonts-noto-cjk fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 cook \
    && useradd --uid 10001 --gid cook --create-home cook \
    && install -d -o cook -g cook /workspace /opt/agent-cook/task-dependencies \
    && ln -s /opt/agent-cook/task-dependencies/node_modules /node_modules

COPY src/agent_cook/__init__.py /usr/local/lib/python3.12/site-packages/agent_cook/__init__.py
COPY src/agent_cook/sandbox/__init__.py src/agent_cook/sandbox/dependencies.py src/agent_cook/sandbox/limits.py src/agent_cook/sandbox/paths.py src/agent_cook/sandbox/urls.py /usr/local/lib/python3.12/site-packages/agent_cook/sandbox/
COPY src/agent_cook/sandbox/worker/*.py /usr/local/lib/python3.12/site-packages/agent_cook/sandbox/worker/

ENV PATH=/home/cook/.local/bin:$PATH
USER cook
WORKDIR /workspace
CMD ["sleep", "infinity"]
