FROM apache/airflow:3.0.3

ARG AIRFLOW_USER=airflow

# ── System dependencies ───────────────────────────────────────────────────────
# This layer is only invalidated when requirements-apt-container.txt changes.
USER root

COPY config/requirements-apt-container.txt /tmp/requirements-apt-container.txt

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y $(grep -v '^#' /tmp/requirements-apt-container.txt | grep -v '^docker' | xargs) && \
    rm /tmp/requirements-apt-container.txt

# ── Python dependencies ───────────────────────────────────────────────────────
# Copy only pyproject.toml first. Docker caches this layer and only re-runs
# pip install when pyproject.toml actually changes — not on every source edit.
USER ${AIRFLOW_USER}

ENV RUNNING_IN_DOCKER=true

WORKDIR /tmp/mediatech

COPY pyproject.toml /tmp/mediatech/pyproject.toml

# Extract and install dependencies listed in pyproject.toml without the package itself.
# pip install --no-deps is used later for the editable install so deps aren't re-resolved.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install $(python3 -c "import tomllib; f=open('pyproject.toml','rb'); deps=tomllib.load(f)['project']['dependencies']; f.close(); print(' '.join(deps))")

# ── Application source code ───────────────────────────────────────────────────
# Copied after pip install so editing source files does not bust the cache above.
USER root
COPY . /tmp/mediatech/
RUN chown -R ${AIRFLOW_USER}:root /tmp/mediatech
USER ${AIRFLOW_USER}

# Editable install of the package itself (deps already present, so this is fast).
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps -e .

# ── Sanity check ──────────────────────────────────────────────────────────────
WORKDIR /opt/airflow
RUN mediatech --help
