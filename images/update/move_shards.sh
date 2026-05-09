#!/bin/bash

SOLR="${SOLR_URL:-http://p87.arquivo.pt:3200}"
COLLECTION="images"
DOMAIN="${DOMAIN}"
PORT="${SOLR_PORT:-3200}"
CSV=$1

if [ -z "$CSV" ]; then
  echo "Usage: $0 <csv_file>"
  exit 1
fi

move_shard() {
  local shard=$1
  local target=$2

  if [ -n "$DOMAIN" ]; then
    local target_node="${target}${DOMAIN}:${PORT}_solr"
  else
    local target_node="${target}:${PORT}_solr"
  fi

  # Check current node
  CURRENT=$(curl -s "${SOLR}/solr/admin/collections?action=CLUSTERSTATUS&collection=${COLLECTION}&wt=json" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    replicas=d['cluster']['collections']['$COLLECTION']['shards']['$shard']['replicas']
    nodes=[r['node_name'] for r in replicas.values()]
    print(nodes[0] if len(nodes)==1 else 'multiple')
except:
    pass
")

  if [ "${CURRENT}" = "${target_node}" ]; then
    echo "$(date) - ${shard} already on ${target}, skipping"
    return 0
  fi

  echo "$(date) - Moving ${shard} from ${CURRENT} to ${target_node}"

  RESULT=$(curl -s "${SOLR}/solr/admin/collections?action=ADDREPLICA&collection=${COLLECTION}&shard=${shard}&node=${target_node}&wt=json")
  echo "  ADDREPLICA: $(echo $RESULT | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['responseHeader']['status'])" 2>/dev/null)"

  echo "  Waiting for replica to become active on ${target}..."
  until curl -s "${SOLR}/solr/admin/collections?action=CLUSTERSTATUS&collection=${COLLECTION}&wt=json" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    replicas=d['cluster']['collections']['$COLLECTION']['shards']['$shard']['replicas']
    active_nodes=[r['node_name'] for r in replicas.values() if r['state']=='active']
    sys.exit(0 if '$target_node' in active_nodes else 1)
except Exception as e:
    sys.exit(1)
"; do
    sleep 5
  done

  # Delete ALL replicas except target
  echo "  Deleting old replicas..."
  curl -s "${SOLR}/solr/admin/collections?action=CLUSTERSTATUS&collection=${COLLECTION}&wt=json" | python3 -c "
import json,sys
d=json.load(sys.stdin)
replicas=d['cluster']['collections']['$COLLECTION']['shards']['$shard']['replicas']
for rname,r in replicas.items():
    if r['node_name'] != '$target_node':
        print(rname)
" | while read old_replica; do
    echo "  Deleting ${old_replica}"
    curl -s "${SOLR}/solr/admin/collections?action=DELETEREPLICA&collection=${COLLECTION}&shard=${shard}&replica=${old_replica}&wt=json" > /dev/null
  done

  echo "$(date) - ${shard} move to ${target} complete"
}
# Read CSV and group moves by target node using temp files
rm -f /tmp/solr_queue_*

while IFS=',' read -r shard target; do
  [[ -z "$shard" || "$shard" == \#* ]] && continue
  shard=$(echo "$shard" | tr -d '[:space:]')
  target=$(echo "$target" | tr -d '[:space:]')
  echo "$shard" >> "/tmp/solr_queue_${target}"
done < "$CSV"

# Process each node's queue sequentially, all nodes in parallel
for qfile in /tmp/solr_queue_*; do
  target=$(basename "$qfile" | sed 's/solr_queue_//')
  (
    echo "$(date) - Starting moves for ${target}"
    while read shard; do
      move_shard "$shard" "$target"
    done < "$qfile"
    rm "$qfile"
    echo "$(date) - All moves for ${target} complete"
  ) &
done

wait
echo "$(date) - All moves complete"

# Final cluster state
echo ""
echo "=== Final cluster state ==="
curl -s "${SOLR}/solr/admin/collections?action=CLUSTERSTATUS&collection=${COLLECTION}&wt=json" | python3 -c "
import json,sys
d=json.load(sys.stdin)
shards=d['cluster']['collections']['$COLLECTION']['shards']
for name,shard in sorted(shards.items()):
    for rname,r in shard['replicas'].items():
        print(f'{name}: {r[\"state\"]} node={r[\"node_name\"]}')
"
