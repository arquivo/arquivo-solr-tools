#!/bin/bash

# generate_backup.sh
# Generates rsync commands to backup a SolrCloud collection across all nodes
#
# Usage:
#   ./generate_backup.sh [options]
#
# Options:
#   -u, --url         Solr URL (default: http://p87.arquivo.pt:3200)
#   -c, --collection  Collection name (default: images)
#   -d, --data-dir    Solr data directory on remote nodes (default: /data/image-search-solr/data)
#   -b, --backup-dir  Backup destination directory (default: /data/solr-backups)
#   -H, --backup-host Remote host for backup destination (default: local). If set, rsync runs on
#                     that host (via ssh -A) and pulls from each solr node — so the backup host
#                     needs SSH access to the solr nodes (agent forwarding from this machine works).
#   -e, --execute     Execute the rsync commands instead of just printing them
#   -h, --help        Show this help message

SOLR_URL="${SOLR_URL:-http://p87.arquivo.pt:3200}"
COLLECTION="${COLLECTION:-images}"
DATA_DIR="${DATA_DIR:-/data/image-search-solr/data}"
BACKUP_DIR="${BACKUP_DIR:-/data/solr-shards-backup/resharded}"
BACKUP_HOST="${BACKUP_HOST:-p121.arquivo.pt}"
EXECUTE=false
DOMAIN="${DOMAIN:-.arquivo.pt}"
SSH_USER="${SSH_USER:-amourao}"

usage() {
  grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case $1 in
    -u|--url)        SOLR_URL="$2"; shift 2 ;;
    -c|--collection) COLLECTION="$2"; shift 2 ;;
    -d|--data-dir)   DATA_DIR="$2"; shift 2 ;;
    -b|--backup-dir) BACKUP_DIR="$2"; shift 2 ;;
    -H|--backup-host) BACKUP_HOST="$2"; shift 2 ;;
    -e|--execute)    EXECUTE=true; shift ;;
    -h|--help)       usage ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

for var in BACKUP_DIR DATA_DIR BACKUP_HOST; do
  val="${!var}"
  if [[ -n "$val" && "$val" == -* ]]; then
    echo "ERROR: $var ('$val') starts with '-' — looks like a flag, did you mean to pass a path?" >&2
    exit 1
  fi
done

echo "# Solr Backup Commands"
echo "# Generated: $(date)"
echo "# Cluster:   ${SOLR_URL}"
echo "# Collection: ${COLLECTION}"
if [ -n "${BACKUP_HOST}" ]; then
  echo "# Backup destination: ${SSH_USER}@${BACKUP_HOST}:${BACKUP_DIR}"
else
  echo "# Backup destination: ${BACKUP_DIR} (local)"
fi
echo ""

# Fetch cluster state
CLUSTER=$(curl -s "${SOLR_URL}/solr/admin/collections?action=CLUSTERSTATUS&collection=${COLLECTION}&wt=json")

if ! echo "$CLUSTER" | python3.9 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
  echo "ERROR: Could not reach Solr at ${SOLR_URL}" >&2
  exit 1
fi

# Parse and generate rsync commands
echo "$CLUSTER" | python3.9 -c "
import json,sys,os

d=json.load(sys.stdin)
shards=d['cluster']['collections']['$COLLECTION']['shards']
domain='$DOMAIN'
ssh_user='$SSH_USER'
data_dir='$DATA_DIR'
backup_dir='$BACKUP_DIR'
backup_host='$BACKUP_HOST'
collection='$COLLECTION'

# Group by node
nodes={}
for shard_name, shard in sorted(shards.items()):
    for rname, r in shard['replicas'].items():
        node=r['node_name'].split(':')[0]
        core=r['core']
        if node not in nodes:
            nodes[node]=[]
        nodes[node].append((shard_name, core))

# Generate rsync commands per node. rsync can't do remote->remote, so when a
# backup_host is set we ssh into it and run rsync there (which pulls from the
# solr node). Agent forwarding (-A) lets the backup host reuse our SSH keys.
def wrap(cmd):
    if backup_host:
        return f'ssh -A {ssh_user}@{backup_host} {cmd!r}'
    return cmd

for node in sorted(nodes.keys()):
    shards_on_node=nodes[node]
    # Strip domain from node name for display
    short_node=node.replace(domain,'') if domain else node
    print(f'# {node} ({len(shards_on_node)} shards)')
    print(wrap(f'mkdir -p {backup_dir}/{short_node}'))
    for shard_name, core in shards_on_node:
        src=f'{ssh_user}@{node}:{data_dir}/{core}/'
        dst=f'{backup_dir}/{short_node}/{core}/'
        print(wrap(f'mkdir -p {dst}'))
        print(wrap(f'rsync -avz --progress -e \"ssh -o StrictHostKeyChecking=accept-new\" {src} {dst}'))
    print()
"

if [ "$EXECUTE" = true ]; then
  echo "# Executing rsync commands..."
  echo "$CLUSTER" | python3.9 -c "
import json,sys,subprocess

d=json.load(sys.stdin)
shards=d['cluster']['collections']['$COLLECTION']['shards']
domain='$DOMAIN'
ssh_user='$SSH_USER'
data_dir='$DATA_DIR'
backup_dir='$BACKUP_DIR'
backup_host='$BACKUP_HOST'

nodes={}
for shard_name, shard in sorted(shards.items()):
    for rname, r in shard['replicas'].items():
        node=r['node_name'].split(':')[0]
        core=r['core']
        if node not in nodes:
            nodes[node]=[]
        nodes[node].append((shard_name, core))

import os

def run_remote(cmd_str):
    # cmd_str runs on backup_host if set, otherwise locally
    if backup_host:
        return ['ssh', '-A', f'{ssh_user}@{backup_host}', cmd_str]
    return ['sh', '-c', cmd_str]

procs=[]
for node in sorted(nodes.keys()):
    short_node=node.replace(domain,'') if domain else node
    for shard_name, core in nodes[node]:
        src=f'{ssh_user}@{node}:{data_dir}/{core}/'
        dst=f'{backup_dir}/{short_node}/{core}/'
        if backup_host:
            subprocess.run(['ssh', '-A', f'{ssh_user}@{backup_host}', f'mkdir -p {dst}'], check=True)
        else:
            os.makedirs(dst, exist_ok=True)
        cmd=run_remote(f'rsync -avz --progress -e \"ssh -o StrictHostKeyChecking=accept-new\" {src} {dst}')
        print(f'Starting: {\" \".join(cmd)}', flush=True)
        procs.append(subprocess.Popen(cmd))

for p in procs:
    p.wait()
print('All rsync commands complete')
"
fi