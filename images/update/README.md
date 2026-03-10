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