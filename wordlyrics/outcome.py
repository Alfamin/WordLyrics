"""A completed process is not necessarily a successfully timed song."""
FAILURES = {"no_lyrics", "network_error", "not_timed", "rejected", "failed", "not_reached", "lines_only"}


def summarize(run):
    counts = {}
    for song in (run.songs if run else []):
        key = song.result.get("status", "not_reached")
        counts[key] = counts.get(key, 0) + 1
    timed = counts.get("timed", 0)
    failed = sum(counts.get(key, 0) for key in FAILURES)
    skipped = counts.get("skipped", 0)
    excluded = len(run.not_usable) + len(getattr(run,"unreadable",[])) if run else 0
    incomplete = bool(failed or excluded or (run and run.check and not run.check["clean"]))
    if timed:
        message = "%d song%s received word timestamps." % (timed, "" if timed == 1 else "s")
    else:
        message = "No new word timestamps were written."
    if failed or excluded:
        message += " %d need attention; see the reasons in the report." % (failed + excluded)
    if skipped:
        message += " %d were left alone; see their skip reasons." % skipped
    if run and run.check and not run.check["clean"]:
        message += " The music folder changed during the run; inspect the final checks in the report."
    return {"timed": timed, "needs_attention": failed + excluded, "skipped": skipped,
            "incomplete": incomplete, "counts": counts, "message": message}
