#!/usr/bin/env bash
set -euo pipefail

# sync_solr_shards.sh
# Usage: ./sync_solr_shards.sh <SOLR_HOST> <SOLR_PORT> <SOLR_COLLECTION>

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <SOLR_HOST> <SOLR_PORT> <SOLR_COLLECTION>" >&2
  exit 1
fi

SOLR_HOST="$1"
SOLR_PORT="$2"
SOLR_COLLECTION="$3"

BASE_URL="http://${SOLR_HOST}:${SOLR_PORT}/solr"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: Required command not found: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd jq

# Helper: GET JSON from Solr Collections API
solr_get_json() {
  local url="$1"
  curl -sS "$url"
}

# Helper: check Solr JSON responseHeader.status == 0
is_ok_response() {
  local json="$1"
  # Some responses may omit responseHeader in rare cases; treat that as failure
  local status
  status="$(echo "$json" | jq -r '.responseHeader.status // empty' 2>/dev/null || true)"
  [[ "$status" == "0" ]]
}

# 1) Fetch cluster status for the collection
CLUSTER_URL="${BASE_URL}/admin/collections?action=CLUSTERSTATUS&collection=${SOLR_COLLECTION}&wt=json"
echo "Fetching cluster status:"
echo "  $CLUSTER_URL"

CLUSTER_JSON="$(solr_get_json "$CLUSTER_URL")"

# Validate the collection exists in response
if ! echo "$CLUSTER_JSON" | jq -e --arg c "$SOLR_COLLECTION" '.cluster.collections[$c]' >/dev/null 2>&1; then
  echo "ERROR: Collection '${SOLR_COLLECTION}' not found in CLUSTERSTATUS response." >&2
  exit 1
fi

# 2) Extract shards list
mapfile -t SHARDS < <(echo "$CLUSTER_JSON" | jq -r --arg c "$SOLR_COLLECTION" '.cluster.collections[$c].shards | keys[]')

if [[ ${#SHARDS[@]} -eq 0 ]]; then
  echo "No shards found for collection '${SOLR_COLLECTION}'. Nothing to do."
  exit 0
fi

echo "Found ${#SHARDS[@]} shard(s) in collection '${SOLR_COLLECTION}'."

# 3) For each shard, find follower replicas (replica entries without leader=true)
#    Replica name here is the key under "replicas" (e.g., core_node37).
for SHARD in "${SHARDS[@]}"; do
  echo
  echo "=== Shard: ${SHARD} ==="

  # Extract follower replica names for this shard.
  # In CLUSTERSTATUS: replicas is a map: { "core_nodeX": { ... "leader":"true"? ... }, ... }
  # Followers usually have no "leader" field at all; leader has leader:true (string or boolean depending on version).
  mapfile -t FOLLOWERS < <(
    echo "$CLUSTER_JSON" | jq -r --arg c "$SOLR_COLLECTION" --arg s "$SHARD" '
      .cluster.collections[$c].shards[$s].replicas
      | to_entries[]
      | select((.value.leader // false) != "true" and (.value.leader // false) != true)
      | .key
    '
  )

  if [[ ${#FOLLOWERS[@]} -eq 0 ]]; then
    echo "No follower replicas found for shard '${SHARD}'. (Leader-only shard?)"
    continue
  fi

  echo "Follower replica(s) to recycle for shard '${SHARD}': ${FOLLOWERS[*]}"

  # 4) Delete each follower, and if successful add a replica back
  for REPLICA in "${FOLLOWERS[@]}"; do
    DELETE_URL="${BASE_URL}/admin/collections?action=DELETEREPLICA&collection=${SOLR_COLLECTION}&shard=${SHARD}&replica=${REPLICA}&wt=json"
    echo
    echo "Deleting follower replica:"
    echo "  shard=${SHARD} replica=${REPLICA}"
    echo "  $DELETE_URL"

    DELETE_JSON="$(solr_get_json "$DELETE_URL")"

    if is_ok_response "$DELETE_JSON"; then
      echo "Delete OK: shard=${SHARD} replica=${REPLICA}"

      ADD_URL="${BASE_URL}/admin/collections?action=ADDREPLICA&collection=${SOLR_COLLECTION}&shard=${SHARD}&wt=json"
      echo "Adding new replica back to shard '${SHARD}':"
      echo "  $ADD_URL"

      ADD_JSON="$(solr_get_json "$ADD_URL")"

      if is_ok_response "$ADD_JSON"; then
        echo "AddReplica OK: shard=${SHARD}"
      else
        echo "ERROR: ADDREPLICA failed for shard=${SHARD}" >&2
        echo "$ADD_JSON" | jq . >&2 || echo "$ADD_JSON" >&2
        exit 1
      fi
    else
      echo "ERROR: DELETEREPLICA failed for shard=${SHARD} replica=${REPLICA}" >&2
      echo "$DELETE_JSON" | jq . >&2 || echo "$DELETE_JSON" >&2
      exit 1
    fi
  done
done

echo
echo "Done. (Note: replica recovery happens asynchronously; monitor CLUSTERSTATUS until replicas become active.)"
