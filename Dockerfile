# One image stands here, built in layers. `toolchain` is what a deck's own build
# runs in: Slidev, the Chromium it exports a PDF with, and nothing else (line
# 14a). `lobby` builds the lobby's SvelteKit app and is thrown away but for its
# output. `instance` is the toolchain layer plus the packaged server and the
# lobby's output; the web and the build worker run from it.
#
# Node is the base because the toolchain's version is the narrow one (the
# engines field of slidev/package.json); uv brings the Python that
# .python-version names. Every image named here is held by digest, because a
# tag is a name its owner may move. uv's tag is read from the `uv` stage by CI
# (.github/actions/setup-uv), so the image and CI run one uv.
#
# The runtime user's uid is named by number in the final USER, because a pod
# with runAsNonRoot can verify a number but not a name.
ARG PRESENTATOR_RUNTIME_UID=1001
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM node:26.12.1-trixie-slim@sha256:94816674c62774da0d099a947bf4613b9e1972a2733abd0d0cd80e795f8411d0 AS runtime-user

# The two identities a build worker runs steps as: one builds, one only reads
# what the build wrote. They are made before the runtime user so that a runtime
# uid equal to either is refused rather than shared. Their uids lie above the
# system's `UID_MAX`, where `--no-log-init` keeps useradd from writing a sparse
# lastlog entry into the layer.
RUN groupadd --gid 61001 presentator-build \
    && useradd --no-log-init --uid 61001 --gid 61001 --home-dir /work/home --no-create-home \
        --shell /usr/sbin/nologin presentator-build \
    && groupadd --gid 61002 presentator-wire \
    && useradd --no-log-init --uid 61002 --gid 61002 --home-dir /nonexistent --no-create-home \
        --shell /usr/sbin/nologin presentator-wire

ARG PRESENTATOR_RUNTIME_UID
RUN case "$PRESENTATOR_RUNTIME_UID" in \
        ''|*[!0-9]*|0*|61001|61002) \
            echo "PRESENTATOR_RUNTIME_UID must be a positive integer without a leading zero, and neither the build user's nor the wire user's uid" >&2; \
            exit 1 ;; \
    esac \
    && test "$PRESENTATOR_RUNTIME_UID" -gt 0 \
    && useradd --create-home --non-unique --uid "$PRESENTATOR_RUNTIME_UID" presentator
# Node 26 no longer bundles corepack, which hands out the pnpm that each
# package.json names in packageManager.
# npm leaves its cache and timestamped logs in root's home, and Node writes its
# compile cache to /tmp; the root steps of the toolchain remove both, because a
# layer that repeats holds neither.
RUN npm install --global corepack@0.36.0 \
    && rm -rf /root/.npm \
    && find /tmp -mindepth 1 -delete

# The browser and corepack's package managers are root's and live outside the
# runtime HOME, so every one of the three users can run them.
ENV HOME=/home/presentator \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    COREPACK_HOME=/opt/corepack

# The lobby is built here alone, so neither its sources nor its toolchain reach
# the deck build environment; Node stays in the image for Slidev only.
FROM runtime-user AS lobby
RUN corepack enable pnpm
WORKDIR /lobby
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/patches ./patches
RUN corepack install && pnpm install --frozen-lockfile
COPY frontend ./
RUN pnpm build

FROM runtime-user AS toolchain

# The toolchain runs as the named unprivileged runtime user.
RUN corepack enable pnpm \
    && find /tmp -mindepth 1 -delete

# The project's dependencies are installed as that user, because a build writes
# its caches beside the project; pnpm and the browser are installed as root.
WORKDIR /app
COPY --chown=presentator slidev/package.json slidev/pnpm-lock.yaml slidev/pnpm-workspace.yaml ./slidev/
WORKDIR /app/slidev
# pnpm 12 fetches its native binary on its first run into the root-owned
# corepack home, so that run happens here as root, before the user switch.
RUN corepack install && pnpm --version \
    && find /tmp -mindepth 1 -delete
# A release builds with SOURCE_DATE_EPOCH, and pnpm then records that time in
# node_modules instead of the clock's. Its store, metadata cache and update-check
# state hold clock times too, and nothing needs them after the install, so the
# same RUN removes them.
ARG SOURCE_DATE_EPOCH
USER presentator
RUN pnpm install --frozen-lockfile \
    && rm -rf ~/.local/share/pnpm ~/.local/state/pnpm ~/.cache/pnpm \
    && find /tmp -mindepth 1 -delete
USER root
# ldconfig's aux-cache and fontconfig's caches record directory mtimes, which a
# release resets, so fontconfig would reject the cache as stale at runtime. A
# read-only root cannot write it back, so Chromium rescans the fonts at every
# launch either way.
RUN pnpm exec playwright install-deps chromium \
    && pnpm exec playwright install chromium \
    && rm -rf /var/lib/apt/lists/* /var/log/apt /var/log/dpkg.log /var/log/alternatives.log \
        /var/cache/ldconfig/aux-cache /var/cache/fontconfig \
        ~/.local/share/pnpm ~/.local/state/pnpm ~/.cache/pnpm \
    && find /tmp -mindepth 1 -delete
# The export's dev server keeps its dependency cache beside Slidev's own
# package, which a read-only root cannot hold, so it points into the step's
# scratch directory.
RUN slidev="$(realpath node_modules/@slidev/cli)" \
    && mkdir -p "$slidev/node_modules" \
    && ln -s /work/vite "$slidev/node_modules/.vite"
USER presentator
COPY --chown=presentator slidev ./
# A step is started with the deck's own command line and nothing else, so the
# project the toolchain resolves from is this image's own place of work.
WORKDIR /app/slidev

FROM toolchain AS instance
ARG PRESENTATOR_RUNTIME_UID
USER root

# git mirrors a deck source and ssh reaches a private one. Their
# versions are the ones this base image's archive carries: Debian keeps no
# older build to fall back to, so a pinned version is a build that breaks the
# day the archive moves, and the digest above is what makes the layer
# repeatable.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/* /var/log/apt /var/log/dpkg.log /var/log/alternatives.log \
        /var/cache/ldconfig/aux-cache

COPY --from=uv /uv /usr/local/bin/uv

# Bytecode stays in the image because the runtime's root is read-only. It
# records its source's hash, not the source's install time, so it repeats.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_COMPILE_BYTECODE=1 \
    PYC_INVALIDATION_MODE=CHECKED_HASH \
    UV_NO_CACHE=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable
COPY --from=lobby /lobby/build /app/lobby

# The directories the instance keeps its state in are made here so that an empty
# volume mounted over one belongs to the user that writes it.
RUN mkdir -p /data/database /data/mirrors /data/builds \
    && chown presentator /data/database /data/mirrors /data/builds

# Where this filesystem keeps what has to survive the container, and the address
# it answers at inside it; the deployment mounts a volume over each directory and
# routes to the port.
ENV PRESENTATOR_DATABASE=/data/database/presentator.sqlite3 \
    PRESENTATOR_MIRRORS=/data/mirrors \
    PRESENTATOR_BUILDS=/data/builds \
    PRESENTATOR_TOOLCHAIN=/app/slidev \
    PRESENTATOR_LOBBY_BUILD=/app/lobby \
    PRESENTATOR_HOST=0.0.0.0 \
    PRESENTATOR_LOCAL_SOURCES_MOUNT=/data/local-sources
USER ${PRESENTATOR_RUNTIME_UID}
WORKDIR /app
EXPOSE 8000
CMD ["presentator"]
