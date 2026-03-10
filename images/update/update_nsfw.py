# Usage:
#   Mark all images belonging to one or more domains as NSFW.
#
#   Example:
#       python update_nsfw.py \
#           pXX.arquivo.pt:8983 \
#           "example.com,example.pt"
#
#   Arguments:
#       host      Solr host (e.g. pXX.arquivo.pt:8983)
#       domains   Comma-separated list of domains to update
#
#   Description:
#       - Selects documents from the Solr "images" core whose pageHost matches
#         one of the provided domains.
#       - For each matching document, sets:
#             safe = 1   (meaning “not safe for work” in your schema)
#             porn = 1   (explicit NSFW classification)
#       - Updates all matching documents in batches of PAGE_SIZE.
#
#   Notes:
#       • This script directly updates Solr (no dry-run).
#       • Does not check the API or use a spreadsheet.
#       • Requires update permissions on the Solr host.

import requests
import sys
import json

PAGE_SIZE = 50000  # Number of documents processed per batch

# Command-line arguments:
#   sys.argv[1] → Solr host
#   sys.argv[2] → comma-separated list of domains
host = sys.argv[1]  # e.g. p44.arquivo.pt:8983
domains = sys.argv[2].split(",")  # e.g. example.com,example.pt

# Build Solr query: match pageHost:<domain> for each domain in the list.
# (Note: This script matches only pageHost, unlike others that match URLs too.)
domain_filter = "pageHost:" + "%20OR%20pageHost:".join(domains).strip()

# Base Solr query to fetch IDs of matching documents
base_query = "http://{}/solr/images/select?q=".format(host) + domain_filter
print(base_query)
r = requests.get(base_query)

# Number of matching documents
counts = r.json()["response"]["numFound"]

# Number of pages needed to retrieve all results
pages = (counts//PAGE_SIZE) + 1
    
for i in range(pages):
    print(counts - i*PAGE_SIZE)  # Progress indicator

    # Fetch next batch of IDs
    r = requests.get("{}&amp;offset={}&amp;fl=id&amp;rows={}".format(base_query, i*PAGE_SIZE, PAGE_SIZE))

    # Build update payload: mark documents as NSFW
    d = [{"id": doc["id"], "safe": {"set": 1}, "porn": {"set": 1}} for doc in r.json()["response"]["docs"]]

    # Send update to Solr
    requests.post("http://{}/solr/images/update?overwrite=true&amp;commit=true".format(host), json=d)