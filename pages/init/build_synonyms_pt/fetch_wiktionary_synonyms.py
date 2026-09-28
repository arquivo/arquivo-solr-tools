#!/usr/bin/env python3
"""Build a Solr synonyms.txt from Wikcionário (Portuguese Wiktionary) "Sinónimos" sections.

Streams the Wikimedia XML dump (bz2), extracts the Portuguese-language
section of each article, then the "Sinónimos"/"Sinônimos" subsections
within it, grouping wikilinks by sense block for precision.

See the header written into the output file for the full methodology.
"""
import argparse
import bz2
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date

import requests

DUMP_URL = "https://dumps.wikimedia.org/ptwiktionary/latest/ptwiktionary-latest-pages-articles-multistream.xml.bz2"
# Wikimedia's dump mirrors reject requests without a descriptive User-Agent (403).
REQUEST_HEADERS = {"User-Agent": "arquivo-solr-tools-synonyms-build/1.0 (https://github.com/arquivo/arquivo-solr-tools)"}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PAGES_INIT_DIR = os.path.dirname(SCRIPT_DIR)
CACHE_DIR = os.path.join(SCRIPT_DIR, ".cache")
DEFAULT_OUTPUT = os.path.join(
    PAGES_INIT_DIR, "solr-configset", "pages", "conf", "lang", "synonyms_pt_wiktionary.txt"
)
# WordNet groups are structurally more precise (grouped by sense/synset), so
# when a group is an exact duplicate of one already there, keep it only in
# that file and drop it here.
DEFAULT_WORDNET_FILE = os.path.join(
    PAGES_INIT_DIR, "solr-configset", "pages", "conf", "lang", "synonyms_pt_wordnet.txt"
)

MIN_GROUP_SIZE = 2
MAX_GROUP_SIZE = 6
PROGRESS_EVERY = 50000

HEADER = """# Portuguese synonym groups derived from Wikcionário (Portuguese Wiktionary).
#
# Source:      https://pt.wiktionary.org (Wikimedia XML dump)
#              {dump_url}
# Retrieved:   {retrieved}
# License:     Creative Commons Attribution-ShareAlike 3.0 (CC BY-SA 3.0) / GFDL
#              Attribution: Wikcionário contributors, https://pt.wiktionary.org
#
# Methodology:
#  1. Streamed the Wikimedia XML dump and, for each article (namespace 0,
#     non-redirect), isolated the Portuguese-language section (the block
#     starting at the "{{{{-pt-}}}}" language marker), since a single
#     Wiktionary page can describe the same word form in several languages.
#  2. Within that section, located "Sinónimos"/"Sinônimos" subsections
#     (both PT-PT/PT-BR spellings are used across entries) and, where the
#     section is split into per-sense blocks ("De '''N''':"), kept each
#     sense block as its own group rather than merging every sense of the
#     headword together - the same precision rationale as grouping by
#     WordNet synset: a synonym is only valid for one specific sense.
#  3. Extracted [[wikilink]] targets (dropping any "#section"/"|display"
#     parts and namespaced links like "Categoria:"), combined with the
#     article's own title to form each group. Rejected any target that
#     isn't plain letters/spaces/hyphens/apostrophes, which filters out
#     wiki-markup leftovers and bound morphemes (e.g. "-ano", "corn(i)-")
#     that occasionally get tagged as "synonyms" of an affix entry.
#  4. Cross-checked against the same page's own "Antonimos"/"Antonimos"
#     section and dropped any term listed there from that page's synonym
#     groups - guards against source entries that sloppily repeat a term
#     under both sections for the same headword (seen in practice, e.g.
#     "gracioso" listing "triste" as both a synonym and an antonym).
#  5. Normalized: lowercased, deduplicated identical groups, dropped groups
#     with fewer than {min_size} members (nothing to synonymize) or more than
#     {max_size} (heuristic against noisy/over-broad sense blocks), sorted
#     deterministically.
#  6. Dropped any group that exactly duplicates one already present in
#     synonyms_pt_wordnet.txt - Solr loads both files together (see the
#     text_pt fieldType's synonymGraph filter), so a group only needs to
#     appear once; the WordNet extraction is kept as the source of record
#     for it since it is grouped by sense/synset rather than by wiki section.
#
# This is a heuristic wiki-markup extraction (not a full wikitext/template
# parser), so residual noise is possible; treat as a first-pass curation.
#
# This file is loaded query-side only via SynonymGraphFilterFactory
# (expand=true) on the text_pt fieldType - no reindex required.
# Format: comma-separated equivalence list, one group per line.
#-----------------------------------------------------------------------
"""

PT_SECTION_RE = re.compile(r"\{\{-pt-\}\}([\s\S]*?)(?=\n\{\{-[a-z-]+-\}\}|\Z)")
SYNONYM_SECTION_RE = re.compile(
    r"=+\s*Sin[oô]nimos\s*=+\n([\s\S]*?)(?=\n=+[^=\n]|\Z)"
)
ANTONYM_SECTION_RE = re.compile(
    r"=+\s*Ant[oô]nimos\s*=+\n([\s\S]*?)(?=\n=+[^=\n]|\Z)"
)
SENSE_SPLIT_RE = re.compile(r"De\s+'''\d+'''\s*:?")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
NAMESPACED_LINK_RE = re.compile(r"^[A-Za-z ]+:")
# Real PT words/phrases only: letters (incl. accented), spaces, apostrophes and
# internal hyphens - rejects wiki-markup leftovers (parens, slashes, braces) and
# bound morphemes such as "-ano" or "corn(i)-".
VALID_TERM_RE = re.compile(r"^[a-zà-öø-ÿ]+([ '-][a-zà-öø-ÿ]+)*$")


def download(url, dest):
    if os.path.exists(dest):
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"Downloading {url} ...", file=sys.stderr)
    with requests.get(url, stream=True, timeout=120, headers=REQUEST_HEADERS) as resp:
        resp.raise_for_status()
        tmp = dest + ".part"
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
        os.replace(tmp, dest)


def normalize_term(term):
    term = term.strip().lower()
    if not term or NAMESPACED_LINK_RE.match(term) or not VALID_TERM_RE.match(term):
        return None
    return term


def extract_groups_from_page(title, text, groups):
    pt_match = PT_SECTION_RE.search(text)
    if not pt_match:
        return
    pt_section = pt_match.group(1)

    norm_title = normalize_term(title)
    if not norm_title:
        return

    # Words the page itself lists as antonyms (of any sense) are excluded from
    # its synonym groups - guards against source entries that sloppily repeat
    # a term under both Sinónimos and Antónimos for the same headword.
    antonyms_on_page = set()
    for ant_match in ANTONYM_SECTION_RE.finditer(pt_section):
        for raw in WIKILINK_RE.findall(ant_match.group(1)):
            norm = normalize_term(raw)
            if norm:
                antonyms_on_page.add(norm)

    for syn_match in SYNONYM_SECTION_RE.finditer(pt_section):
        content = syn_match.group(1)
        blocks = SENSE_SPLIT_RE.split(content) if SENSE_SPLIT_RE.search(content) else [content]
        for block in blocks:
            members = {norm_title}
            for raw in WIKILINK_RE.findall(block):
                norm = normalize_term(raw)
                if norm and norm not in antonyms_on_page:
                    members.add(norm)
            if MIN_GROUP_SIZE <= len(members) <= MAX_GROUP_SIZE:
                groups.add(tuple(sorted(members)))


def iter_pages(dump_path):
    with bz2.open(dump_path, "rb") as fh:
        context = ET.iterparse(fh, events=("end",))
        title = None
        ns = None
        for _event, elem in context:
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag == "title":
                title = elem.text
            elif tag == "ns":
                ns = elem.text
            elif tag == "redirect":
                title = None  # skip redirects
            elif tag == "text":
                if title is not None and ns == "0" and elem.text:
                    yield title, elem.text
                title = None
            elif tag == "page":
                elem.clear()


def load_existing_groups(path):
    groups = set()
    if not os.path.exists(path):
        return groups
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                groups.add(tuple(sorted(line.split(","))))
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--wordnet-file", default=DEFAULT_WORDNET_FILE)
    parser.add_argument("--cache-dir", default=CACHE_DIR)
    args = parser.parse_args()

    dump_path = os.path.join(args.cache_dir, "ptwiktionary-latest-pages-articles-multistream.xml.bz2")
    download(DUMP_URL, dump_path)

    groups = set()
    count = 0
    for title, text in iter_pages(dump_path):
        extract_groups_from_page(title, text, groups)
        count += 1
        if count % PROGRESS_EVERY == 0:
            print(f"...processed {count} articles, {len(groups)} groups so far", file=sys.stderr)

    wordnet_groups = load_existing_groups(args.wordnet_file)
    deduped = groups - wordnet_groups
    dropped = len(groups) - len(deduped)

    sorted_groups = sorted(deduped)
    print(
        f"Processed {count} articles, built {len(sorted_groups)} synonym groups "
        f"({dropped} dropped as exact duplicates of synonyms_pt_wordnet.txt)",
        file=sys.stderr,
    )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(
            HEADER.format(
                dump_url=DUMP_URL,
                retrieved=date.today().isoformat(),
                min_size=MIN_GROUP_SIZE,
                max_size=MAX_GROUP_SIZE,
            )
        )
        for group in sorted_groups:
            fh.write(",".join(group) + "\n")

    print(f"Wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
