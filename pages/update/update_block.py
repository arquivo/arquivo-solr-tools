#!/usr/bin/env python3
"""Block documents in the pages index from a block-list CSV (Blacklist.csv or a
Google Sheets CSV export with the same layout: URL pattern in the first column).

Sets blocked=1 on every document whose `surts` value matches an entry. Entries
are matched regardless of any timestamp/timerange columns: the whole document
is blocked.

Entry types (first CSV column):
  domain            sub.example.pt            -> SURT prefix query (subdomains included)
  url with path     example.pt/some/path/     -> SURT prefix query (exact host, path prefix)
  wildcard          foo.(.*).example.pt/      -> Solr regexp query on surts

Usage:
    python update_block.py --host p44.arquivo.pt:8983 --csv Blacklist.csv [--collection pages]
    python update_block.py --host localhost:8983 --collection page --csv Blacklist.csv --dry-run
    python update_block.py --host localhost:8983 --csv "https://docs.google.com/spreadsheets/d/<id>/export?format=csv" --dry-run
"""

import argparse
import csv
import io
import sys

import requests

PAGE_SIZE = 50000
WILDCARD = "(.*)"
WILDCARD_PLACEHOLDER = "\x00"

# Lucene RegExp special characters that must be escaped in literal parts.
# '/' delimits the regexp term in the query syntax, so it needs escaping too.
LUCENE_REGEX_SPECIALS = '\\.?+*|{}[]()"~<>#@&/'


def read_entries(csv_source):
    """Read URL patterns from the first column of the block-list CSV.

    csv_source may be a local path or an http(s) URL (Google Sheets export).
    Returns deduplicated patterns in file order.
    """
    if csv_source.startswith(("http://", "https://")):
        resp = requests.get(csv_source, timeout=60)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        f = io.StringIO(resp.text)
    else:
        f = open(csv_source, newline="", encoding="utf-8")
    entries = []
    seen = set()
    with f:
        for row in csv.reader(f):
            if not row:
                continue
            url = row[0].strip().lower()
            # skip header and junk rows
            if not url or url.startswith("url") or " " in url:
                continue
            if url in seen:
                continue
            seen.add(url)
            entries.append(url)
    return entries


def split_host_path(url):
    url = url.strip()
    for scheme in ("https://", "http://"):
        if url.startswith(scheme):
            url = url[len(scheme):]
    if "/" in url:
        host, path = url.split("/", 1)
        path = "/" + path
    else:
        host, path = url, ""
    if host.startswith("www."):
        host = host[4:]
    return host, path


def surt_prefix(url):
    """'sub.example.pt/some/path' -> '(pt,example,sub,)/some/path'.

    Without a path the prefix stops at the host labels ('(pt,example,sub,')
    so subdomains are included; with a path the host is closed ('...,)') and
    the path is matched as a prefix.
    """
    host, path = split_host_path(url)
    parts = [p for p in host.split(".") if p]
    parts.reverse()
    if path and path != "/":
        return "(" + ",".join(parts) + ",)" + path
    return "(" + ",".join(parts) + ","


def escape_regex(text):
    return "".join("\\" + c if c in LUCENE_REGEX_SPECIALS else c for c in text)


def surt_regex(url):
    """Wildcard entry -> Lucene regexp matching the surts value.

    'foo.(.*).example.pt/' -> \\(pt,example,.*,foo,\\).*
    'foo(.*).sub.example.pt/' -> \\(pt,example,sub,foo.*,\\).*
    """
    host, path = split_host_path(url)
    host = host.replace(WILDCARD, WILDCARD_PLACEHOLDER)
    parts = [p for p in host.split(".") if p]
    parts.reverse()
    host_re = ",".join(escape_regex(p).replace(WILDCARD_PLACEHOLDER, ".*") for p in parts)
    path_re = escape_regex(path) if path and path != "/" else ""
    # Lucene regexps are anchored: trailing .* matches the rest of the surt
    return "\\(" + host_re + ",\\)" + path_re + ".*"


def entry_to_fq(url):
    """Return (kind, fq) for a block-list entry."""
    if WILDCARD in url:
        return "regexp", "surts:/" + surt_regex(url) + "/"
    # {!prefix} treats the value literally, so '(' needs no escaping
    return "prefix", "{!prefix f=surts}" + surt_prefix(url)


def solr_select(select_url, fq_list, rows, fl=None, sort=None):
    params = {"q": "*:*", "fq": fq_list, "rows": rows, "wt": "json"}
    if fl:
        params["fl"] = fl
    if sort:
        params["sort"] = sort
    r = requests.get(select_url, params=params, timeout=300)
    r.raise_for_status()
    return r.json()["response"]


def block_entry(select_url, update_url, fq, dry_run):
    """Count matches for one entry and, unless dry_run, set blocked=1 on them.

    Pagination is done by repeatedly fetching the first page of still-unblocked
    matches (fq=-blocked:1) and committing each batch — stable under the
    reordering that atomic updates cause, and resumable if interrupted.
    """
    matched = solr_select(select_url, [fq], 0)["numFound"]
    remaining = solr_select(select_url, [fq, "-blocked:1"], 0)["numFound"]
    already = matched - remaining
    updated = 0

    if not dry_run:
        while remaining > 0:
            resp = solr_select(select_url, [fq, "-blocked:1"], PAGE_SIZE,
                               fl="id", sort="id asc")
            docs = resp["docs"]
            if not docs:
                break
            updates = [{"id": doc["id"], "blocked": {"set": 1}} for doc in docs]
            r = requests.post(update_url + "?commit=true", json=updates, timeout=600)
            r.raise_for_status()
            updated += len(updates)
            new_remaining = solr_select(select_url, [fq, "-blocked:1"], 0)["numFound"]
            if new_remaining >= remaining:
                raise RuntimeError(
                    f"blocked count not decreasing ({remaining} -> {new_remaining}); "
                    f"aborting to avoid an infinite loop"
                )
            remaining = new_remaining

    return matched, already, updated


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", required=True, help="Solr host:port, e.g. p44.arquivo.pt:8983")
    parser.add_argument("--csv", required=True,
                        help="block-list CSV: local path or http(s) URL of a Google Sheets export")
    parser.add_argument("--collection", default="pages",
                        help="Solr collection name (default: pages; local deploys use 'page')")
    parser.add_argument("--dry-run", action="store_true",
                        help="only report per-entry match counts; write nothing")
    args = parser.parse_args()

    select_url = f"http://{args.host}/solr/{args.collection}/select"
    update_url = f"http://{args.host}/solr/{args.collection}/update"

    entries = read_entries(args.csv)
    print(f"{len(entries)} unique block entries from {args.csv}")
    if args.dry_run:
        print("DRY RUN — no documents will be updated\n")

    header = f"{'kind':<7} {'matched':>9} {'already':>9} {'updated':>9}  entry"
    print(header)
    print("-" * len(header))

    total_matched = total_already = total_updated = failures = 0
    for url in entries:
        kind, fq = entry_to_fq(url)
        try:
            matched, already, updated = block_entry(select_url, update_url, fq, args.dry_run)
        except Exception as e:
            failures += 1
            print(f"{kind:<7} {'ERROR':>9} {'':>9} {'':>9}  {url}  ({e})", file=sys.stderr)
            continue
        total_matched += matched
        total_already += already
        total_updated += updated
        print(f"{kind:<7} {matched:>9} {already:>9} {updated:>9}  {url}")

    print("-" * len(header))
    print(f"{'total':<7} {total_matched:>9} {total_already:>9} {total_updated:>9}")
    if failures:
        print(f"{failures} entries failed — see stderr", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
