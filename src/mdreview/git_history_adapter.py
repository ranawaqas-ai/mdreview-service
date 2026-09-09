"""mdreview-side adapter for git_history (#379): translates ReviewService/CommentService's
on-disk shape into the generic git_history.interfaces.HistorySource protocol.

Deliberately lives HERE, not inside git_history: the design's IoC boundary says git_history never
imports mdreview.reviews or mdreview.store, so the translation has to happen on this side of the
seam. Read-only — touches none of the write path (put_source/snapshot_round untouched, per the
plan's non-goals) and uses only ReviewService/CommentService's existing public reads.
"""
import json
import os

from git_history.interfaces import Snapshot


class MdreviewHistorySource:
    def __init__(self, reviews, comments):
        self._reviews = reviews
        self._comments = comments

    def list_snapshots(self, doc_id):
        """Oldest-first Snapshot per historical round: source.md + notes.json + feedback.md,
        exactly what snapshot_round() archives under history/round-N/. comments.json is
        deliberately NOT here (it is not part of a round snapshot today; see current() below).

        Iterates the ACTUAL round numbers reviews.history() reports rather than range(0, count):
        history_round() already tolerates a missing round by returning None (skipped here), so a
        gap never breaks the whole materialize."""
        rounds = sorted(self._reviews.history(doc_id), key=lambda r: r.get("round", 0))
        out = []
        for entry in rounds:
            n = entry.get("round")
            data = self._reviews.history_round(doc_id, n)
            if data is None:
                continue
            author = data.get("by") or "agent"
            files = {
                "source.md": (data.get("source") or "").encode("utf-8"),
                "notes.json": json.dumps(data.get("notes") or [], indent=2).encode("utf-8"),
                "feedback.md": (data.get("feedback") or "").encode("utf-8"),
            }
            out.append(Snapshot(files=files, author=author, email="%s@mdreview.local" % author,
                                 ts=data.get("ts") or 0.0, message="round %s" % n))
        return out

    def current(self, doc_id):
        """The live tip: source.md + notes.json + feedback.md + comments.json. comments.json is
        current-only by design (viewer-authored comments since MR-036 live outside the round
        mechanism; making them git-versionable too would mean changing snapshot_round() itself,
        which the plan deliberately avoids)."""
        d = self._reviews.store.dir(doc_id)
        source = self._reviews.read_source(doc_id)
        notes = self._reviews.store.read_json(os.path.join(d, "notes.json"), [])
        feedback = self._reviews.store.read_text(os.path.join(d, "feedback.md"))
        comments = self._comments.list(doc_id)
        files = {
            "source.md": source.encode("utf-8"),
            "notes.json": json.dumps(notes, indent=2).encode("utf-8"),
            "feedback.md": feedback.encode("utf-8"),
            "comments.json": json.dumps(comments, indent=2).encode("utf-8"),
        }
        meta = self._reviews.meta(doc_id)
        author = meta.get("source_updated_by") or "agent"
        ts = meta.get("source_updated") or 0.0
        return Snapshot(files=files, author=author, email="%s@mdreview.local" % author, ts=ts,
                         message="current")
