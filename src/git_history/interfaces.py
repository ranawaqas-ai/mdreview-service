"""The IoC seam (#379): the ONLY thing git_history depends on to reach a host's document model.

This package never imports mdreview (or anything host-specific) anywhere — a host wires it by
implementing HistorySource and injecting an authorize callable (see __init__.py:build). That
keeps git_history testable with a fake in-memory source and reusable by any tool with a similar
"sequence of document snapshots" model.
"""


class Snapshot:
    """One point-in-time state of a document: a set of files plus commit attribution.

    files    - {relative path -> bytes}. Whatever the host wants versioned; git_history has no
               opinion on names or count (the mdreview adapter puts source.md/notes.json/
               feedback.md in every snapshot, plus comments.json in current() only).
    author   - a display name for the commit author (never blank; the adapter defaults it).
    email    - a synthetic commit-author email (git requires an email-shaped string; a host with
               no real one can use anything plausible, e.g. "<author>@<host>.local").
    ts       - epoch seconds for the commit date.
    message  - the commit message.
    """
    __slots__ = ("files", "author", "email", "ts", "message")

    def __init__(self, files, author="agent", email="agent@example.invalid", ts=0.0, message=""):
        self.files = files
        self.author = author
        self.email = email
        self.ts = ts
        self.message = message or "snapshot"


class HistorySource:
    """Protocol (duck-typed, not enforced): a host implements these two reads.

    list_snapshots(doc_id) -> oldest-first list[Snapshot] of every IMMUTABLE historical state.
        gitcache.py treats this list as append-only and never rebuilds a commit once made, so a
        host must never mutate or reorder a snapshot already returned for a given doc_id.
    current(doc_id) -> Snapshot, the live/tip state. Read fresh on every call (never cached) so a
        re-clone right after a live edit is never stale.
    """

    def list_snapshots(self, doc_id):
        raise NotImplementedError

    def current(self, doc_id):
        raise NotImplementedError
