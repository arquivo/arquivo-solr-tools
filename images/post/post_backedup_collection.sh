#! /bin/bash
PROGNAME=$0

# Path towards hadoop output folder & backup folder. Edit this if needed.
BACKUP_FOLDER=/data/images/to_backup/pipe # Where the backup is stored
SCRIPTS_FOLDER=/opt/solr-cloud-scripts/images/post
TMP_FOLDER=$(mktemp -d "/data/tmp_XXXXXX")

usage() {
  cat << EOF >&2
Usage: $PROGNAME <solr_host> <solr_port> <solr_collection> <collection>

 <solr_host>: The server where solr is hosted
 <solr_port>: The port where solr is hosted
 <solr_collection>: The solr collection (images for image search and text_index_v6 for text search)
 <collection>: The backed up collection we want to post

This script will post a compressed backed up collection into solr.

Example:
$PROGNAME p87.arquivo.pt 3200 images Roteiro

EOF
}

exit_error() {
  rm -rf "$TMP_FOLDER"
  exit 1
}


if [[ $# -ne 4 ]]; then
    echo "Error: expected 4 arguments, got $#." >&2
    usage
    exit_error
fi

SOLR_HOST=$1
SOLR_PORT=$2
SOLR_COLLECTION=$3
COLLECTION=$4


if [[ ! -d "$BACKUP_FOLDER/$COLLECTION" ]]; then
    echo "Error: Could not find backup folder - $BACKUP_FOLDER/$COLLECTION doesn't exist" >&2
    exit_error
fi

COMPRESSED_FILES=()
while IFS= read -r -d '' FILE; do
    COMPRESSED_FILES+=("$FILE")
done < <(find "$BACKUP_FOLDER/$COLLECTION" -type f -name "*.tar.gz" -print0)

if (( ${#COMPRESSED_FILES[@]} == 0 )); then
    echo "Error: No compressed files found on $BACKUP_FOLDER/$COLLECTION"
    exit_error
else
    for FILE in "${COMPRESSED_FILES[@]}"; do
      rm -rf "$TMP_FOLDER/*"
      echo "Decompressing $FILE..."
      tar xzf "$FILE" -C "$TMP_FOLDER"
      find "$TMP_FOLDER" -type f -name "part-r*" | sort > "$TMP_FOLDER"/toPost.txt
      head -n3 "$TMP_FOLDER"/toPost.txt
      echo "..."
      tail -n3 "$TMP_FOLDER"/toPost.txt
      echo "Posting..."
      python3.9 "$SCRIPTS_FOLDER"/incremental_post.py "$SOLR_HOST" "$SOLR_PORT" "$SOLR_COLLECTION" "$TMP_FOLDER"/toPost.txt
    done
fi

rm -rf "$TMP_FOLDER"
