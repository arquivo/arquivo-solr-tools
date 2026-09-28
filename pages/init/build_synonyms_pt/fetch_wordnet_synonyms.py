#!/usr/bin/env python3
"""Build a Solr synonyms.txt from OpenWordnet-PT synset data.

Downloads the two OpenWordnet-PT data files needed to reconstruct synonym
groups (words that share a synset, i.e. a specific sense) and writes a
curated, deduplicated Solr synonyms.txt to the pages configset.

See the header written into the output file for the full methodology.
"""
import argparse
import os
import re
import sys
from datetime import date

import requests

WORDSENSES_URL = "https://raw.githubusercontent.com/own-pt/openWordnet-PT/master/data/own-pt-wordsenses.ttl"
WORDS_URL = "https://raw.githubusercontent.com/own-pt/openWordnet-PT/master/data/own-pt-words.ttl"
REQUEST_HEADERS = {"User-Agent": "arquivo-solr-tools-synonyms-build/1.0 (https://github.com/arquivo/arquivo-solr-tools)"}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PAGES_INIT_DIR = os.path.dirname(SCRIPT_DIR)
CACHE_DIR = os.path.join(SCRIPT_DIR, ".cache")
DEFAULT_OUTPUT = os.path.join(
    PAGES_INIT_DIR, "solr-configset", "pages", "conf", "lang", "synonyms_pt_wordnet.txt"
)

MIN_GROUP_SIZE = 2
MAX_GROUP_SIZE = 6

HEADER = """# Portuguese synonym groups derived from OpenWordnet-PT.
#
# Source:      https://github.com/own-pt/openWordnet-PT
#              (data/own-pt-wordsenses.ttl + data/own-pt-words.ttl)
# Retrieved:   {retrieved}
# License:     Creative Commons Attribution 4.0 International (CC BY 4.0)
#              Attribution: OpenWordnet-PT (Rademaker, de Paiva, Aguiar et al.),
#              https://github.com/own-pt/openWordnet-PT
#
# Methodology:
#  1. Parsed the RDF/Turtle triples linking each synset to its word senses
#     (owns:containsWordSense) and each word sense to its lemma
#     (owns:word -> owns:lemma), then grouped lemmas by the synset they
#     belong to. Synset IDs already encode part of speech (-n/-v/-a/-r),
#     so every group below is POS-pure: it lists words for one shared
#     *sense*, not a raw flat thesaurus entry, which is what keeps this
#     more precise than merging whole (polysemous) lemmas together.
#  2. Normalized each lemma: lowercased, underscores in multiword lemmas
#     turned into spaces (e.g. "a_capela" -> "a capela").
#  3. Dropped synsets with fewer than {min_size} distinct lemmas (nothing to
#     synonymize) or more than {max_size} (a heuristic against loosely-related,
#     overly broad synsets, which hurt precision if expanded at query time).
#  4. Deduplicated identical groups and sorted the result deterministically.
#
# This file is loaded query-side only via SynonymGraphFilterFactory
# (expand=true) on the text_pt fieldType - no reindex required.
# Format: comma-separated equivalence list, one group per line.
#-----------------------------------------------------------------------
"""

WORDSENSE_BLOCK_RE = re.compile(
    r"own-pt:(synset-\d+-[nvar])\s+owns:containsWordSense\s+([\s\S]*?)\s\.", re.MULTILINE
)
WORDSENSE_REF_RE = re.compile(r"own-pt:(wordsense-[^\s,;.]+)")

WORD_LEMMA_RE = re.compile(
    r'own-pt:(word-[^\s]+)\s+a\s+owns:Word\s*;\s*\n\s*owns:lemma\s+"([^"]*)"@pt'
)
WORDSENSE_TO_WORD_RE = re.compile(r"own-pt:(wordsense-[^\s]+)\s+owns:word\s+own-pt:(word-[^\s]+)\s*\.")


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


def normalize_lemma(lemma):
    return lemma.strip().replace("_", " ").lower()


def build_groups(wordsenses_ttl, words_ttl):
    word_to_lemma = {}
    for word_id, lemma in WORD_LEMMA_RE.findall(words_ttl):
        norm = normalize_lemma(lemma)
        if norm:
            word_to_lemma[word_id] = norm

    wordsense_to_word = dict(WORDSENSE_TO_WORD_RE.findall(words_ttl))

    groups = set()
    for _synset_id, block in WORDSENSE_BLOCK_RE.findall(wordsenses_ttl):
        wordsense_ids = WORDSENSE_REF_RE.findall(block)
        lemmas = set()
        for ws_id in wordsense_ids:
            word_id = wordsense_to_word.get(ws_id)
            lemma = word_to_lemma.get(word_id) if word_id else None
            if lemma:
                lemmas.add(lemma)
        if MIN_GROUP_SIZE <= len(lemmas) <= MAX_GROUP_SIZE:
            groups.add(tuple(sorted(lemmas)))

    return sorted(groups)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", default=CACHE_DIR)
    args = parser.parse_args()

    wordsenses_path = os.path.join(args.cache_dir, "own-pt-wordsenses.ttl")
    words_path = os.path.join(args.cache_dir, "own-pt-words.ttl")
    download(WORDSENSES_URL, wordsenses_path)
    download(WORDS_URL, words_path)

    with open(wordsenses_path, encoding="utf-8") as fh:
        wordsenses_ttl = fh.read()
    with open(words_path, encoding="utf-8") as fh:
        words_ttl = fh.read()

    groups = build_groups(wordsenses_ttl, words_ttl)
    print(f"Built {len(groups)} synonym groups", file=sys.stderr)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(
            HEADER.format(
                retrieved=date.today().isoformat(), min_size=MIN_GROUP_SIZE, max_size=MAX_GROUP_SIZE
            )
        )
        for group in groups:
            fh.write(",".join(group) + "\n")

    print(f"Wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
