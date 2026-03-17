import requests
import sys

PAGE_SIZE = 50000
host = sys.argv[1]       # e.g. p44.arquivo.pt:8983
domains = sys.argv[2].split(",")  # e.g. example.com,example.pt

def domain_to_surt_prefix(domain: str) -> str:
    """
    Convert a domain to its SURT prefix.
    example.com      -> com,example,
    www.example.com  -> com,example,        (www is stripped)
    sub.example.com  -> com,example,sub,    (subdomains kept as-is)
    """
    parts = domain.strip().lower().split(".")
    # Strip leading 'www' for the canonical prefix match
    if parts[0] == "www":
        parts = parts[1:]
    parts.reverse()
    return ",".join(parts) + ","

for domain in domains:
    surt_prefix = domain_to_surt_prefix(domain)

    # surts is multiValued; use a prefix query on the field
    # fq keeps it fast (filter cache); q=*:* avoids scoring overhead
    base_query = (
        "http://{}/solr/pages/select"
        "?q=*:*"
        "&fq=surts:{surt_prefix}*"
        "&fl=id"
    ).format(host, surt_prefix=surt_prefix)

    r = requests.get(base_query + "&rows=0")
    r.raise_for_status()
    counts = r.json()["response"]["numFound"]
    pages = (counts // PAGE_SIZE) + 1
    print(f"Domain: {domain}  SURT prefix: {surt_prefix}  Found: {counts}")

    for i in range(pages):
        start = i * PAGE_SIZE
        remaining = counts - start
        print(f"  Fetching rows {start}–{start + min(PAGE_SIZE, remaining) - 1} …")

        r = requests.get(f"{base_query}&start={start}&rows={PAGE_SIZE}")
        r.raise_for_status()
        docs = r.json()["response"]["docs"]

        updates = [{"id": doc["id"], "blocked": {"set": 1}} for doc in docs]
        resp = requests.post(
            f"http://{host}/solr/pages/update?overwrite=true&commit=true",
            json=updates,
        )
        resp.raise_for_status()
        print(f"  Committed {len(updates)} updates.")