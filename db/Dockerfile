FROM paradedb/paradedb:latest-pg17

# Install pgvectorscale on top of ParadeDB (which already includes pgvector + pg_search)
# ParadeDB is based on PostgreSQL 17

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates curl \
    build-essential \
    postgresql-server-dev-17 \
    pkg-config libssl-dev \
    git cmake && \
    # Install Rust (needed to build pgvectorscale)
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    export PATH="$HOME/.cargo/bin:$PATH" && \
    # Install cargo-pgrx matching the version used by pgvectorscale
    cargo install --locked cargo-pgrx --version 0.12.5 && \
    cargo pgrx init --pg17 $(which pg_config) && \
    # Clone and build pgvectorscale
    git clone --branch 0.6.0 https://github.com/timescale/pgvectorscale.git /tmp/pgvectorscale && \
    cd /tmp/pgvectorscale/pgvectorscale && \
    cargo pgrx install --release && \
    # Cleanup to reduce image size
    cd / && rm -rf /tmp/pgvectorscale && \
    rustup self uninstall -y && \
    apt-get purge -y build-essential postgresql-server-dev-17 pkg-config libssl-dev git cmake && \
    apt-get autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
