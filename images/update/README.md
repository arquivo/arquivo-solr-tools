# Images Update Scripts

## Installation

```
pip install -r requirements.txt
```

---

## `block_images.py`

This script reads blocking rules from a Google Spreadsheet and:

- Checks Solr servers for matching unblocked documents  
- Optionally applies blocking updates  
- Verifies whether blocked content appears in the imagesearch API

### Usage

#### 1. Block all matching images in Solr (no API needed)

```
python block_images.py     \
    --solr "pXX.arquivo.pt:8983,pYY.arquivo.pt:8983" \
    --pathjson service_account.json \
    --key <GOOGLE_SHEET_KEY> \
    --worksheet "Requests" \
    --no-api
```

#### 2. Check whether the API is serving blocked content (no Solr needed)

```
python block_images.py     \
    --api "arquivo.pt/imagesearch" \
    --pathjson service_account.json \
    --key <GOOGLE_SHEET_KEY> \
    --worksheet "Requests" \
    --no-solr
```

#### 3. Dry-run Solr check (no API needed, do not block anything)

```
python block_images.py     \
    --solr "pXX.arquivo.pt:8983,pYY.arquivo.pt:8983" \
    --pathjson service_account.json \
    --key <GOOGLE_SHEET_KEY> \
    --worksheet "Requests" \
    --no-solr-update \
    --no-api
```
---
## `update_block.py`

Blocks all Solr image documents for one or more domains.

### Usage

```
python update_block.py <SOLR_HOST> "<DOMAIN_1,DOMAIN_2,...>"
```

### Example

```
python update_block.py pXX.arquivo.pt:8983 "example.com,example.pt"
```

---

## `update_docs_by_collection.py`

(Documentation to be added.)

---

## `update_nsfw.py`

Sets all Solr image documents for one or more domains as NSFW.

### Usage

```
python update_nsfw.py <SOLR_HOST> "<DOMAIN_1,DOMAIN_2,...>"
```

### Example

```
python update_nsfw.py pXX.arquivo.pt:8983 "example.com,example.pt"
```

# move_shards.sh

Moves Solr shards across nodes in a SolrCloud cluster based on a CSV mapping file.
Moves to different nodes run in parallel, while moves to the same node are serialized
to avoid overwhelming individual nodes with concurrent replica copies.

## Requirements

- `bash` 4+
- `curl`
- `python3`
- Network access to the Solr cluster

## Usage

```bash
./move_shards.sh <csv_file>
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SOLR_URL` | `http://p87.arquivo.pt:3200` | URL of any Solr node in the cluster |
| `DOMAIN` | `.arquivo.pt` | Domain suffix appended to node names. Set to `''` for local/Docker clusters |
| `SOLR_PORT` | `3200` | Solr port used when building node names for ZooKeeper |

## CSV Format

One shard move per line: `<shard_name>,<target_node>`

- Lines starting with `#` are treated as comments and ignored
- Whitespace around values is stripped
- Shards already on the target node are skipped automatically

### Example

```csv
# Move shards to balance across nodes
shard1_0,p87
shard1_1,p87
shard2_0,p79
shard2_1,p79
shard5_1,p82
shard7_0,p82
```

## Behavior

- **Idempotent**: if a shard is already on the target node it is skipped with no changes made
- **Semi-async**: moves to different nodes run in parallel; moves to the same node are serialized
- **Safe**: ADDREPLICA is called first and the script waits for the new replica to become active before deleting the old one, ensuring no data loss
- **Self-cleaning**: all replicas on non-target nodes are deleted after the move, including stale replicas from previous failed runs

## Examples

### Production (arquivo.pt)

```bash
./move_shards.sh distribution.csv
```

### Local Docker test cluster

```bash
SOLR_URL=http://localhost:8981 DOMAIN='' SOLR_PORT=8983 ./move_shards.sh move_test.csv
```

## Output

```
Sat May  9 10:34:42 WEST 2026 - Starting moves for p82
Sat May  9 10:34:42 WEST 2026 - Moving shard5_1 from p87.arquivo.pt:3200_solr to p82.arquivo.pt:3200_solr
  ADDREPLICA: 0
  Waiting for replica to become active on p82...
  Deleting old replica core_node303
Sat May  9 10:34:55 WEST 2026 - shard5_1 move to p82 complete
Sat May  9 10:34:55 WEST 2026 - All moves for p82 complete
Sat May  9 10:34:55 WEST 2026 - All moves complete

=== Final cluster state ===
shard1_0: active node=p87.arquivo.pt:3200_solr
shard1_1: active node=p87.arquivo.pt:3200_solr
...
```

## Notes

- Each shard is ~50GB so each move takes a few minutes depending on disk and network speed
- Run inside a `screen` or `tmux` session for long-running migrations
- The final cluster state is printed at the end of the script