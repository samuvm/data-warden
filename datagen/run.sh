#!/usr/bin/env bash
# End-to-end build of one CIERZO profile.
#
#   ./datagen/run.sh dev        # ~15 s
#   ./datagen/run.sh demo       # minutes
#   ./datagen/run.sh full       # the published dataset
#
# Three passes, and the order matters: the authorization facts are generated
# first, the money-movement facts are DERIVED from them in SQL so they reconcile
# by construction, and the catalogue is then rebuilt so the derived tables appear
# as views alongside everything else.
set -euo pipefail
PROFILE="${1:-dev}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="uv run --with-requirements ${ROOT}/datagen/requirements.txt python"
DATA="${ROOT}/datagen/out/${PROFILE}"
DB="${ROOT}/datagen/out/cierzo-${PROFILE}.duckdb"

echo "==> 1/7  generating authorization facts (${PROFILE})"
$RUN "${ROOT}/datagen/generate.py" --profile "${PROFILE}" --out "${ROOT}/datagen/out"

echo "==> 2/7  cataloguing"
$RUN "${ROOT}/datagen/build_duckdb.py" --data "${DATA}" --db "${DB}" >/dev/null

echo "==> 3/7  deriving settlement, payout, refund and dispute"
$RUN "${ROOT}/datagen/build_derived.py" --data "${DATA}" --db "${DB}" --end-date 2026-08-31

echo "==> 4/7  rebuilding catalogue and verifying"
$RUN "${ROOT}/datagen/build_duckdb.py" --data "${DATA}" --db "${DB}"

echo "==> 5/7  Iceberg spec v2 (registra el Parquet, no lo copia)"
$RUN "${ROOT}/datagen/build_iceberg.py" --data "${DATA}" --rebuild

echo "==> 6/7  measured figures (datagen/MEASURED-${PROFILE}.md)"
$RUN "${ROOT}/datagen/report.py" --data "${DATA}" --db "${DB}" \
     --out "${ROOT}/datagen/MEASURED-${PROFILE}.md"

echo "==> 7/7  portable catalogue script for the container"
$RUN "${ROOT}/datagen/build_duckdb.py" --data "${DATA}" \
     --emit-sql "${ROOT}/datagen/docker/catalog.sql" --glob-root /warehouse/data
