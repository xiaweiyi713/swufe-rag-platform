#!/bin/sh
set -eu

# metadata.sqlite3 runs compatibility migrations during startup. Keep the
# checked-in/mounted source database immutable and migrate a container-local
# copy instead; SQLite WAL and journal files stay in the writable /tmp volume.
source_metadata="${SWUFE_RAG_METADATA:-/app/data/metadata.sqlite3}"
runtime_dir="${SWUFE_RAG_RUNTIME_DIR:-/tmp/swufe-rag}"
runtime_metadata="$runtime_dir/metadata.sqlite3"

if [ ! -f "$source_metadata" ]; then
  echo "Missing metadata database: $source_metadata" >&2
  exit 1
fi

mkdir -p "$runtime_dir"
cp "$source_metadata" "$runtime_metadata"
export SWUFE_RAG_METADATA="$runtime_metadata"

exec "$@"
