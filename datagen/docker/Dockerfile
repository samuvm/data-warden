# A SQL shell with the CIERZO warehouse already attached.
#
# The image carries the DuckDB CLI and nothing else; the data is mounted at run
# time. That keeps the image at a few tens of megabytes and, more importantly,
# means the container cannot go stale: regenerate the Parquet on the host and the
# next `docker compose run` sees it.
FROM debian:bookworm-slim

ARG DUCKDB_VERSION=1.5.5
# TARGETARCH is filled in by BuildKit. Hard-coding linux-amd64 builds an image
# that runs under emulation on an Apple Silicon host -- it works, and it is
# roughly four times slower on exactly the machine this dataset is meant to be
# explored from.
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) DUCK_ARCH=linux-amd64 ;; \
      arm64) DUCK_ARCH=linux-arm64 ;; \
      *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unzip \
 && curl -fsSL -o /tmp/duckdb.zip \
      "https://github.com/duckdb/duckdb/releases/download/v${DUCKDB_VERSION}/duckdb_cli-${DUCK_ARCH}.zip" \
 && unzip -q /tmp/duckdb.zip -d /usr/local/bin \
 && chmod +x /usr/local/bin/duckdb \
 && apt-get purge -y curl unzip \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* /tmp/duckdb.zip

WORKDIR /warehouse

# The startup scripts are MOUNTED, not copied in. Baking them into the image
# means every change to the catalogue needs an image rebuild, and a stale
# `init.sql` inside the image silently wins over the corrected one on disk --
# which is exactly how this file came to be written this way.
#
# No database path is given, so DuckDB runs in memory: there is nothing in this
# container that can write to the warehouse it is exploring.
ENTRYPOINT ["duckdb", "-init", "/etc/cierzo/init.sql"]
