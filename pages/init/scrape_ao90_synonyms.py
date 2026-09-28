#!/usr/bin/env python3
# Usage:
#   Scrape the AO90 (1990 Portuguese orthographic agreement) "Vocabulário de
#   Mudança" from portaldalinguaportuguesa.org and generate a Solr synonyms.txt
#   with bidirectional pre/post-reform spelling equivalence groups
#   (e.g. "acção, ação"), for use by a synonymGraph filter on the
#   query-side text_pt analyzer.
#
#   Example:
#       python3 scrape_ao90_synonyms.py
#       python3 scrape_ao90_synonyms.py --output /tmp/ao90_synonyms.txt --delay 1
#
#   Arguments:
#       --output   Path to write the generated synonyms file to.
#                  (default: solr-configset/pages/conf/lang/ao90_synonyms.txt,
#                  relative to this script)
#       --version  Site "version" query param: pe (Portugal, default),
#                  pb (Brazil), or all.
#       --delay    Seconds to sleep between per-letter requests (default: 0.5).
#
#   Description:
#       - Fetches https://www.portaldalinguaportuguesa.org/recursos.html
#         (action=novoacordo&act=list) once per letter of the alphabet.
#       - Parses the "Ortografia Antiga" / "Ortografia Nova" table rows.
#         The "new" column sometimes lists multiple accepted spellings
#         separated by commas (e.g. "setor, sector") - all of these join the
#         same equivalence group as the old form.
#       - Merges rows that share a spelling into a single connected group, so
#         a synonym class is always fully bidirectional, then writes each
#         group as one comma-separated Solr synonym line.
#       - Pairs that become identical once lowercased (e.g. "Abril"/"abril")
#         carry no information for a query analyzer that already lowercases
#         first, so they're written out as commented-out lines with an
#         explanation instead of being silently dropped.
#
#   Notes:
#       - Network access required. No third-party dependencies.
#       - Idempotent: re-running overwrites the output file with a fresh scrape.

import argparse
import html
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE_URL = "https://www.portaldalinguaportuguesa.org/recursos.html"
LETTERS = "abcdefghijklmnopqrstuvwxyz"
USER_AGENT = "Mozilla/5.0 (compatible; arquivo-pt-solr-tools/1.0)"
ROW_RE = re.compile(
    r"<tr [^>]*><td title='forma antiga'>([^<]*)<td>([^<]*)<td>[^<]*<p>"
)
DEFAULT_OUTPUT = (
    Path(__file__).parent
    / "solr-configset"
    / "pages"
    / "conf"
    / "lang"
    / "ao90_synonyms.txt"
)


def fetch_letter(letter, version, delay):
    url = f"{BASE_URL}?action=novoacordo&act=list&letter={letter}&version={version}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        body = response.read().decode("utf-8")
    time.sleep(delay)
    return body


def parse_rows(body):
    for old_raw, new_raw in ROW_RE.findall(body):
        old = html.unescape(old_raw).strip()
        alternatives = [html.unescape(part).strip() for part in new_raw.split(",")]
        yield old, [alt for alt in alternatives if alt]


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, item):
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_a] = root_b


def build_synonym_groups(rows):
    union_find = UnionFind()
    noop_comments = []

    for old, alternatives in rows:
        forms = [old] + alternatives
        distinct_lower = {form.lower() for form in forms}
        if len(distinct_lower) == 1:
            # Exact-dedup (not just casefold) so we don't print e.g. "abril, abril".
            seen, display_forms = set(), []
            for form in forms:
                if form not in seen:
                    seen.add(form)
                    display_forms.append(form)
            noop_comments.append(
                f"# {', '.join(display_forms)} "
                "# not added because our analyzer already ignores case."
            )
            continue

        lower_forms = sorted(distinct_lower)
        first = lower_forms[0]
        for form in lower_forms[1:]:
            union_find.union(first, form)

    groups = {}
    for form in union_find.parent:
        groups.setdefault(union_find.find(form), set()).add(form)

    data_lines = [
        ", ".join(sorted(members)) for members in groups.values() if len(members) > 1
    ]
    return sorted(data_lines), sorted(noop_comments)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--version", default="pe", choices=["pe", "pb", "all"])
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args()

    rows = []
    for letter in LETTERS:
        print(f"Fetching letter '{letter}'...", file=sys.stderr)
        body = fetch_letter(letter, args.version, args.delay)
        rows.extend(parse_rows(body))

    data_lines, noop_comments = build_synonym_groups(rows)

    with args.output.open("w", encoding="utf-8") as f:
        f.write(
            "# AO90 (1990 orthographic agreement) pre/post-reform spelling synonyms.\n"
            "# Scraped from portaldalinguaportuguesa.org's \"Vocabulário de Mudança\"\n"
            f"# (version={args.version}) by scrape_ao90_synonyms.py - do not edit by\n"
            "# hand, re-run the script instead.\n"
            "#\n"
            "# Format: comma-separated equivalence groups, expanded bidirectionally\n"
            "# by the synonymGraph filter (expand=true).\n\n"
        )
        f.write("\n".join(data_lines))
        f.write("\n\n")
        f.write(
            "# --- Skipped: identical once lowercased "
            "(already handled by the analyzer's LowerCaseFilter) ---\n"
        )
        f.write("\n".join(noop_comments))
        f.write("\n")

    print(
        f"Wrote {len(data_lines)} synonym groups and {len(noop_comments)} "
        f"skipped no-op pairs to {args.output}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
