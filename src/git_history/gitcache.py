"""Lazy git materializer (#379): builds/refreshes one bare repo per document id from a
HistorySource, via `git` plumbing subprocess calls only (hash-object/update-index/write-tree/
commit-tree/update-ref) — no working tree, no third-party git library (this repo is stdlib-only;
dulwich/pygit2 are both pip packages and were ruled out in the design).

Historical snapshots are committed ONCE, in order, and never rebuilt (immutable once built) — the
`first_index`/`built` accounting below exists only to make that append resumable across calls. The
tip commit is different: it is REPLACED (not appended) on every ensure_repo call whenever
current()'s content or the historical parent has changed, so a clone right after a live edit is
never stale, without the object database growing a new commit for every no-op poll.

Cap semantics (MDREVIEW_GIT_MATERIALIZE_MAX_ROUNDS): only the FIRST ever materialize decides the
window. If a document already has more historical snapshots than the cap the first time its repo
is built, only the most recent `max_rounds` are committed (a permanently shallow history) — the
older ones are never walked, which is what actually bounds the cost of one request. Once a repo
has a history, new snapshots simply append one at a time as they arrive; re-windowing an
already-built chain would mean rebuilding "immutable" commits, which this deliberately never does.
"""
import json
import os
import re
import subprocess
import tempfile
import threading

_MAIN_REF = "refs/heads/main"
_BASE_REF = "refs/git-history/base"          # last purely-historical commit, or absent if none yet
_RID_SAFE = re.compile(r"^[A-Za-z0-9]{4,40}$")


class GitCache:
    def __init__(self, cache_dir, max_rounds):
        self._cache_dir = cache_dir
        self._max_rounds = max_rounds
        self._locks_guard = threading.Lock()
        self._locks = {}

    def repo_path(self, doc_id):
        return os.path.join(self._cache_dir, doc_id + ".git")

    def _meta_path(self, doc_id):
        return os.path.join(self._cache_dir, doc_id + ".meta.json")

    def _lock_for(self, doc_id):
        with self._locks_guard:
            lock = self._locks.get(doc_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[doc_id] = lock
            return lock

    def ensure_repo(self, doc_id, source):
        """Build/refresh doc_id's bare repo from `source` (a HistorySource) and return its path.

        Serialized per doc_id (a module-level lock, never mdreview's store.lock — this plan
        deliberately never touches that seam) so two concurrent clone requests for the SAME review
        can't race the same working index file or ref update; different reviews proceed in
        parallel, unserialized."""
        if not _RID_SAFE.match(doc_id):
            raise ValueError("invalid doc_id %r" % doc_id)
        with self._lock_for(doc_id):
            return self._ensure_repo_locked(doc_id, source)

    def _ensure_repo_locked(self, doc_id, source):
        os.makedirs(self._cache_dir, exist_ok=True)
        repo = self.repo_path(doc_id)
        meta_path = self._meta_path(doc_id)
        if not (os.path.isdir(repo) and os.path.isfile(meta_path)):
            _init_bare(repo)
            _write_json(meta_path, {})

        meta = _read_json(meta_path, {})
        first_index = meta.get("first_index")

        snapshots = source.list_snapshots(doc_id)
        total = len(snapshots)

        base_sha = _rev_parse(repo, _BASE_REF)
        if first_index is None:
            first_index = max(0, total - self._max_rounds) if total else 0
            base_sha = None

        built = _rev_list_count(repo, _BASE_REF) if base_sha else 0
        next_index = first_index + built
        appended = False
        parent = base_sha
        for i in range(next_index, total):
            snap = snapshots[i]
            parent = _commit_snapshot(repo, snap, parent)
            appended = True
        if appended:
            _update_ref(repo, _BASE_REF, parent)
        base_sha = parent

        _write_json(meta_path, {"first_index": first_index})

        cur = source.current(doc_id)
        tree_sha = _write_tree(repo, cur.files)
        tip_sha = _rev_parse(repo, _MAIN_REF)
        tip_tree = _rev_parse(repo, "%s^{tree}" % tip_sha) if tip_sha else None
        if appended or tip_sha is None or tip_tree != tree_sha:
            new_tip = _commit_tree(repo, tree_sha, base_sha, cur.author, cur.email, cur.ts,
                                    cur.message)
            _update_ref(repo, _MAIN_REF, new_tip)
        return repo


# ---- git plumbing (subprocess only; no working tree) ----------------------------------------

def _run(repo, args, env_extra=None, input_bytes=None):
    env = dict(os.environ)
    env["GIT_DIR"] = repo
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(["git"] + args, env=env, input=input_bytes,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args, proc.stderr.decode("utf-8", "replace")))
    return proc.stdout


def _init_bare(repo):
    os.makedirs(repo, exist_ok=True)
    _run(repo, ["init", "--quiet", "--bare"])
    _run(repo, ["symbolic-ref", "HEAD", _MAIN_REF])
    # Read-only clone only (#379 non-goal: no push-back-into-mdreview). Explicit, not just implied
    # by never wiring a receive-pack route: http-backend enables receive-pack for an authenticated
    # REMOTE_USER, which this repo's config now refuses regardless.
    _run(repo, ["config", "http.receivepack", "false"])


def _rev_parse(repo, ref):
    try:
        return _run(repo, ["rev-parse", "--verify", "--quiet", ref]).decode().strip() or None
    except RuntimeError:
        return None


def _rev_list_count(repo, ref):
    try:
        return int(_run(repo, ["rev-list", "--count", ref]).decode().strip())
    except RuntimeError:
        return 0


def _update_ref(repo, ref, sha):
    _run(repo, ["update-ref", ref, sha])


def _write_tree(repo, files):
    fd, index_path = tempfile.mkstemp(prefix="idx-", dir=os.path.dirname(repo) or None)
    os.close(fd)
    os.remove(index_path)          # git update-index wants to create it itself
    try:
        env = {"GIT_INDEX_FILE": index_path}
        for path, content in sorted(files.items()):
            if not isinstance(content, (bytes, bytearray)):
                content = str(content).encode("utf-8")
            sha = _run(repo, ["hash-object", "-w", "--stdin"], input_bytes=bytes(content)).decode().strip()
            _run(repo, ["update-index", "--add", "--cacheinfo", "100644", sha, path], env_extra=env)
        return _run(repo, ["write-tree"], env_extra=env).decode().strip()
    finally:
        try:
            os.remove(index_path)
        except OSError:
            pass


def _commit_snapshot(repo, snap, parent_sha):
    tree_sha = _write_tree(repo, snap.files)
    return _commit_tree(repo, tree_sha, parent_sha, snap.author, snap.email, snap.ts, snap.message)


def _commit_tree(repo, tree_sha, parent_sha, author, email, ts, message):
    date = "%d +0000" % int(ts or 0)
    env = {
        "GIT_AUTHOR_NAME": author or "agent", "GIT_AUTHOR_EMAIL": email or "agent@example.invalid",
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_NAME": author or "agent", "GIT_COMMITTER_EMAIL": email or "agent@example.invalid",
        "GIT_COMMITTER_DATE": date,
    }
    args = ["commit-tree", tree_sha]
    if parent_sha:
        args += ["-p", parent_sha]
    args += ["-m", message or "snapshot"]
    return _run(repo, args, env_extra=env).decode().strip()


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)
