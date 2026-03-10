# Purpose: Posts JSONL files into Solr
#!/bin/bash

# index_text_bash.sh
# Usage: ./index_text_bash.sh <SOLR_COLLECTION> <COLLECTION_FILE> <SOLR_HOST> <SOLR_PORT>

LOG_BASEDIR=./log
POST_MAX_BYTES=100M

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <SOLR_COLLECTION> <COLLECTION_FILE> <SOLR_HOST> <SOLR_PORT>" >&2
  echo "   - SOLR_COLLECTION: The name of the solr collection (e.g.: pages)" >&2
  echo "   - COLLECTION_FILE: Path to a file with a list of hadoop output files to be indexed (e.g.: toPost.txt)" >&2
  echo "   - SOLR_HOST: Server hosting Solr (e.g.: p114.arquivo.pt)" >&2
  echo "   - SOLR_PORT: Port hosting Solr (e.g.: 2200)" >&2
  exit 1
fi

COLLECTION=$1
COLLECTION_FILE=$2
HOST=$3
PORT=$4

LOG_DIR="$LOG_BASEDIR"/$(date +"%Y-%m-%d_%Hh%Mm%S")_$(head -n1 "$COLLECTION_FILE" | rev | cut -d/ -f2 | rev)
mkdir -p "$LOG_DIR"

while IFS="" read -r row || [ -n "$row" ]; do

  # Create per-file temp dir under /data
  TMPDIR=$(mktemp -d /data/solr_tmp_XXXXXX)

  # Split into chunks inside the temp dir
  split -C "$POST_MAX_BYTES" "$row" "$TMPDIR/chunk_"

  # Process each chunk

  chunks=( "$TMPDIR"/chunk_* )
  total=${#chunks[@]}
  i=0

  for chunk in "${chunks[@]}"; do
    i=$((i+1))
    echo "Processing chunk $i/$total"

    curl "http://$HOST:$PORT/solr/$COLLECTION/update/json/docs?update.chain=script&overwrite=false&commit=false" \
        --data-binary @"$chunk" \
        -H 'Content-type: application/json'
  done

  # Logging
  LOG_FILE_PATH="$LOG_DIR/$(basename "$row")_metrics.json"
  wget "http://$HOST:$PORT/solr/admin/cores?wt=json" -O "$LOG_FILE_PATH"

  # Cleanup: delete the temp directory and chunks
  rm -rf "$TMPDIR"

done < "$COLLECTION_FILE"
echo "Committing..."
curl "http://$HOST:$PORT/solr/$COLLECTION/update/json?commit=true"

# Logging
LOG_FILE_PATH="$LOG_DIR/final_metrics.json"
wget "http://$HOST:$PORT/solr/admin/cores?wt=json" -O "$LOG_FILE_PATH"
