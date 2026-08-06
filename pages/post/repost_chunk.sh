#!/bin/bash
# repost_chunk.sh
#
# Purpose: Repair a single failed posting chunk from index_text_bash.sh by
# regenerating it from the original part-r file (split is deterministic),
# stripping out any malformed document(s) by ID (if known), and reposting
# only that chunk. This avoids reposting already-successful documents, which
# would create duplicates since the original posting uses overwrite=false.
#
# Usage: ./repost_chunk.sh <SOLR_COLLECTION> <SOLR_HOST> <SOLR_PORT> <PART_R_FILE> <CHUNK_INDEX> [DOC_ID ...]
#   - SOLR_COLLECTION: The name of the solr collection (e.g.: pages)
#   - SOLR_HOST:      Server hosting Solr (e.g.: p114.arquivo.pt)
#   - SOLR_PORT:      Port hosting Solr (e.g.: 2200)
#   - PART_R_FILE:    Path to the original hadoop part-r file (same "row" that was passed to index_text_bash.sh)
#   - CHUNK_INDEX:    1-based index of the failed chunk, matching the "Processing chunk i/total" log line
#   - DOC_ID:         Optional - one or more unique IDs of malformed documents to strip from the chunk
#                      before reposting. Omit entirely for a chunk that failed with no associated
#                      document id (e.g. a transport-level timeout) - the whole chunk is reposted unchanged.
#
# Argument order is deliberately COLLECTION/HOST/PORT first so this can be driven directly from the
# awk failure-report via xargs, e.g.:
#   <awk_command> | xargs -L1 ./repost_chunk.sh pages p44.arquivo.pt 2200
# where each awk output line is: "<PART_R_FILE> <CHUNK_INDEX> [DOC_ID ...]"
#
# NOTE: Must match the POST_MAX_BYTES used by index_text_bash.sh for the split to
# be byte-identical to the original chunk boundaries.

set -u

POST_MAX_BYTES=100M
ID_FIELD='"id"'

if [[ $# -lt 5 ]]; then
  echo "Usage: $0 <SOLR_COLLECTION> <SOLR_HOST> <SOLR_PORT> <PART_R_FILE> <CHUNK_INDEX> [DOC_ID ...]" >&2
  exit 1
fi

COLLECTION=$1
HOST=$2
PORT=$3
PART_R_FILE=$4
CHUNK_INDEX=$5
shift 5
DOC_IDS=("$@")

if [[ ! -f "$PART_R_FILE" ]]; then
  echo "ERROR: part-r file not found: $PART_R_FILE" >&2
  exit 1
fi

# Regenerate the exact same chunks the original posting run produced.
TMPDIR=$(mktemp -d /data/solr_tmp_repair_XXXXXX)
echo "Regenerating chunks from $PART_R_FILE into $TMPDIR ..."
split -C "$POST_MAX_BYTES" "$PART_R_FILE" "$TMPDIR/chunk_"

chunks=( "$TMPDIR"/chunk_* )
total=${#chunks[@]}

if (( CHUNK_INDEX < 1 || CHUNK_INDEX > total )); then
  echo "ERROR: chunk index $CHUNK_INDEX out of range (1-$total for this file)" >&2
  rm -rf "$TMPDIR"
  exit 1
fi

TARGET_CHUNK="${chunks[$((CHUNK_INDEX-1))]}"
echo "Target chunk: $TARGET_CHUNK (chunk $CHUNK_INDEX/$total)"

REPAIRED_CHUNK="$TMPDIR/repaired_chunk"
cp "$TARGET_CHUNK" "$REPAIRED_CHUNK"

if (( ${#DOC_IDS[@]} > 0 )); then
  lines_before=$(wc -l < "$REPAIRED_CHUNK")

  # Build a grep pattern file so we can strip all bad doc IDs in one pass.
  PATTERN_FILE="$TMPDIR/bad_ids.txt"
  : > "$PATTERN_FILE"
  for id in "${DOC_IDS[@]}"; do
    echo "${ID_FIELD}:\"${id}\"" >> "$PATTERN_FILE"
  done

  grep -vFf "$PATTERN_FILE" "$REPAIRED_CHUNK" > "$REPAIRED_CHUNK.tmp"
  mv "$REPAIRED_CHUNK.tmp" "$REPAIRED_CHUNK"

  lines_after=$(wc -l < "$REPAIRED_CHUNK")
  removed=$((lines_before - lines_after))

  echo "Lines before: $lines_before, after: $lines_after, removed: $removed (expected: ${#DOC_IDS[@]})"

  if [[ "$removed" -ne "${#DOC_IDS[@]}" ]]; then
    echo "WARNING: removed line count does not match number of DOC_IDs given." >&2
    echo "         Verify the ID field name ($ID_FIELD) and DOC_ID values before proceeding." >&2
    echo "         Repaired chunk left at: $REPAIRED_CHUNK for manual inspection." >&2
    exit 1
  fi
else
  echo "No doc IDs given - reposting chunk $CHUNK_INDEX unchanged (e.g. transport-level failure)."
fi

echo "Reposting chunk to http://$HOST:$PORT/solr/$COLLECTION ..."
# NOTE: no --max-time/timeout on purpose. Reposts can take minutes (or, for large
# chunks/slow shards, hours). Do not add a timeout here - a killed repost is worse
# than a slow one, since it can leave a chunk in an unknown, hard-to-diagnose state.
#
# NOTE: update.chain=script is required, not optional. In pages' solrconfig.xml the
# "script" chain (update-script.js) is non-default, so Solr silently skips it if this
# param is omitted. That script is what lets a document keep track of every document
# collection it appears in, instead of the most recent (re)post overwriting the rest.
curl "http://$HOST:$PORT/solr/$COLLECTION/update/json/docs?update.chain=script&overwrite=false&commit=false" \
    --data-binary @"$REPAIRED_CHUNK" \
    -H 'Content-type: application/json'

echo
echo "Done. Remember to run a commit once all repairs for this run are posted:"
echo "  curl \"http://$HOST:$PORT/solr/$COLLECTION/update/json?commit=true\""

rm -rf "$TMPDIR"
