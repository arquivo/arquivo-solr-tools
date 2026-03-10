# Usage:
#   Bulk‑block all Solr image documents belonging to one or more domains.
#
#   Example:
#       python update_block.py pXX.arquivo.pt:8983 "example.com,example.pt"
#
#   Arguments:
#       host      Solr host (e.g. pXX.arquivo.pt:8983)
#       domains   Comma-separated list of domains to block
#
#   Description:
#       For each domain:
#         - Builds a Solr query matching pageHost/pageUrl/imgUrl variants
#         - Retrieves all image documents in batches
#         - Sets blocked=1 on every matching document
#       This script performs no API checks, timestamp filtering, or dry-run mode.
#
#   Requires:
#       - Network access to the Solr host
#       - Python packages: requests
#       - Permission to update Solr documents (blocked=1)
#
#   WARNING:
#       This script immediately applies blocking updates to Solr.
#       Use with caution.

import requests
import sys
import json

PAGE_SIZE = 50000  # Number of Solr documents to update in each batch

# Command-line arguments:
#   sys.argv[1] → Solr host (e.g. pXX.arquivo.pt:8983)
#   sys.argv[2] → Comma-separated list of domains (e.g. example.com,example.pt)
host = sys.argv[1]
domains = sys.argv[2].split(",")

for domain in domains:

    # Build a Solr filter that matches documents whose pageHost, pageUrl or imgUrl
    # start with the given domain or its 'www.' version, for both http and https.
    domain_filter = "pageHost:{0}*%20OR%20pageUrl:https\\:\\/\\/{0}*%20OR%20imgUrl:https\\:\\/\\/{0}*%20OR%20pageUrl:http\\:\\/\\/{0}*%20OR%20imgUrl:http\\:\\/\\/{0}*%20OR%20pageHost:www.{0}*%20OR%20pageUrl:https\\:\\/\\/www.{0}*%20OR%20imgUrl:https\\:\\/\\/www.{0}*%20OR%20pageUrl:http\\:\\/\\/www.{0}*%20OR%20imgUrl:http\\:\\/\\/www.{0}*".format(domain)

    # Query Solr for all matching documents
    base_query = "http://{}/solr/images/select?q=".format(host) + domain_filter
    r = requests.get(base_query)
    print(base_query)

    # Number of documents found
    counts = r.json()["response"]["numFound"]

    # Determine number of fetch pages needed
    pages = (counts//PAGE_SIZE) + 1
        
    for i in range(pages):
        # Print remaining items (approximate progress)
        print(counts - i*PAGE_SIZE)

        # Fetch IDs of the next PAGE_SIZE matching documents
        r = requests.get("{}&amp;offset={}&amp;fl=id&amp;rows={}".format(base_query, i*PAGE_SIZE, PAGE_SIZE))

        # Build update payload to set blocked=1 on each document
        d = [{"id": doc["id"], "blocked": {"set": 1}} for doc in r.json()["response"]["docs"]]

        # Send update request to Solr with commit=true
        requests.post("http://{}/solr/images/update?overwrite=true&amp;commit=true".format(host), json=d)