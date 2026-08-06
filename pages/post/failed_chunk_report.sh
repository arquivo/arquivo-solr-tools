#!/bin/bash
# Script made to analyse errors that happen during the massive posting of pagesearch to solr.
#
# Usage: failed_chunks_report.sh | xargs -L1 ./repost_chunk.sh pages p125.arquivo.pt 2200 | tee repost.log
#
# Will read post.log and output <file_name> <chunk_number> [doc_id1 ...] so it can be piped
#   into the repost_chunk.sh script, which will handle reposting the failed chunks.
#
# This was specifically tailored to read the post.log file created by the following command:
#   cat allCollections | xargs -I {} bash -c 'echo "=== POSTING {} ===" && find {} -type f -name "part*" | sort > toPost.txt && ./index_text_bash.sh pages toPost.txt p125 2200' | tee post.log 2>&1
#
# Note: reposts can themselves fail (e.g. transient timeouts) - check repost.log for
# further errors. This script won't work on repost.log directly (it lacks the
# "Processing chunk"/"=== POSTING ===" markers this parsing depends on).
#
# In the future it would be nice for the post script to handle these errors by itself, but
#   there are advantages in reposting the failed chunks separately:
#   1 - Errors while posting don't hinder the posting process as a whole, the chunk just
#         gets discarded and the process moves on. Handling these errors within the posting
#         script if not done carefully could cause the script to halt.
#   2 - It keeps the posting script simple and straightforward, which is great for long
#         term maintainability. Handling chunks that caused errors may prove to be complex
#         and by doing it separately we ensure the core process is simple and robust.
#   3 - Currently (at the time of writing this) the repost_chunk.sh script is only able to
#         handle two different kinds of error:
#          - One malformed document compromising the whole chunk, which is solved by just
#              reposting the chunk excluding the malformed document.
#          - A perfectly valid chunk fails to post due to random fluctuations in stuff like
#              Solr load, or network connectivity, etc. This is solved by reposting the
#              chunk.
#       This approach proved to work for the dev cluster, however down the line we might
#         find other errors that aren't solved by these heuristics alone. Handling this
#         separately from the main script gives us flexibility to work on the fixes
#         without compromising the core posting script.

grep -E 'error":|Exception writing document id|===|Processing chunk' post.log | awk '
function flush_chunk() {
    if (chunk_had_error) {
        line_out = ERR_FILE " " ERR_CHUNK
        for (i = 1; i <= n_ids; i++) {
            line_out = line_out " " ids[i]
        }
        print line_out
    }
    chunk_had_error = 0
    n_ids = 0
    delete ids
}
/===/ {
    flush_chunk()
    dir = $0
    sub(/.*=== POSTING /, "", dir)
    sub(/ ===.*/, "", dir)
    PART_DIR = dir
    PART_COUNT = -1
    CHUNK_COUNT = 0
    next
}
/Processing chunk [0-9]+\// {
    flush_chunk()
    chunk = $0
    sub(/.*chunk /, "", chunk)
    sub(/\/.*/, "", chunk)
    CHUNK_COUNT = chunk
    if (CHUNK_COUNT == 1) {
        PART_COUNT++
    }
    # "part-r-00NNN" is Hadoop/MapReduce output file naming (e.g. part-r-00000,
    # part-r-00001, ...), reconstructed here in the same numeric order that
    # `find {} -type f -name "part*" | sort` fed into index_text_bash.sh.
    ERR_FILE = sprintf("%s/part-r-00%03d", PART_DIR, PART_COUNT)
    ERR_CHUNK = CHUNK_COUNT
    next
}
/error":/ {
    chunk_had_error = 1
    next
}
/Exception writing document id/ {
    line = $0
    while (match(line, /Exception writing document id [^ ]+/)) {
        token = substr(line, RSTART, RLENGTH)
        id = token
        sub(/.*Exception writing document id /, "", id)
        n_ids++
        ids[n_ids] = id
        chunk_had_error = 1
        line = substr(line, RSTART + RLENGTH)
    }
    next
}
END {
    flush_chunk()
}
'
