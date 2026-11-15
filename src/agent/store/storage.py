"""Where the SQLite file lives between runs.

LocalStorage: the file just stays on disk (VPS).
GitBranchStorage: the file is gzipped and kept on a dedicated branch (GitHub Actions),
optionally with small extra files such as the news context. Each push replaces the branch with a single commit so
the repository does not grow with every run.
GitBackupStorage: the agent on Ali's own server (2026-11-15). The file stays on disk and is the
source of truth; the branch is only a backup (and the news worker's context), refreshed every few
minutes, so runs every minute don't move ~30 MB through GitHub each time.
"""
from __future__ import annotations

import gzip
import os
import sqlite3
import subprocess
import time
from pathlib import Path


class LocalStorage:
    def __init__(self, db_path: str | Path, extra_dbs: list[str | Path] = ()):
        self.db_path = Path(db_path)

    def pull(self) -> bool:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return self.db_path.exists()

    def push(self, message: str = "", extra: dict[str, bytes] | None = None) -> None:
        for name, data in (extra or {}).items():
            (self.db_path.parent / name).write_bytes(data)


class GitBranchStorage:
    FILE = "state.db.gz"

    def __init__(self, db_path: str | Path, branch: str = "data", remote: str = "origin",
                 repo_dir: str | Path = ".", extra_dbs: list[str | Path] = ()):
        """extra_dbs: further SQLite files kept on the branch as <name>.gz (system 2)."""
        self.db_path = Path(db_path)
        self.extra_dbs = [Path(p) for p in extra_dbs]
        self.branch = branch
        self.remote = remote
        self.repo_dir = Path(repo_dir)

    def _git(self, *args: str, input: bytes | None = None, env: dict | None = None) -> bytes:
        return subprocess.run(["git", *args], cwd=self.repo_dir, input=input, env=env,
                              check=True, capture_output=True).stdout

    def pull(self) -> bool:
        """Download the database from the branch. Returns False if the branch doesn't exist yet."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._git("fetch", "--depth=1", self.remote, self.branch)
            blob = self._git("show", f"FETCH_HEAD:{self.FILE}")
        except subprocess.CalledProcessError:
            return False
        self.db_path.write_bytes(gzip.decompress(blob))
        for path in self.extra_dbs:
            try:
                path.write_bytes(gzip.decompress(self._git("show", f"FETCH_HEAD:{path.name}.gz")))
            except subprocess.CalledProcessError:       # not created yet
                pass
        return True

    def push(self, message: str = "Update state", extra: dict[str, bytes] | None = None) -> None:
        """extra: small public files to keep next to the database (e.g. the news context
        the Cloudflare worker reads)."""
        files = {self.FILE: gzip.compress(_snapshot(self.db_path), mtime=0), **(extra or {})}
        for path in self.extra_dbs:
            if path.exists():
                files[f"{path.name}.gz"] = gzip.compress(_snapshot(path), mtime=0)
        lines = ""
        for name in sorted(files):
            blob = self._git("hash-object", "-w", "--stdin", input=files[name]).decode().strip()
            lines += f"100644 blob {blob}\t{name}\n"
        tree = self._git("mktree", input=lines.encode()).decode().strip()
        env = {**os.environ,
               "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "trade-signal-agent"),
               "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "agent@users.noreply.github.com")}
        env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
        env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
        commit = self._git("commit-tree", tree, "-m", message, env=env).decode().strip()
        self._git("push", "--force", self.remote, f"{commit}:refs/heads/{self.branch}")


def _snapshot(path: Path) -> bytes:
    """A consistent copy of a database another process may be writing (run-funnel):
    SQLite's backup API, never the raw file mid-write."""
    src = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(":memory:")
    try:
        src.backup(dst)
        return dst.serialize()
    except sqlite3.DatabaseError:                   # not a database (a test file): as it is
        return Path(path).read_bytes()
    finally:
        src.close()
        dst.close()


class GitBackupStorage(GitBranchStorage):
    STAMP = ".last_backup"

    def __init__(self, *args, every_min: float = 15, **kw):
        super().__init__(*args, **kw)
        self.every_s = every_min * 60
        self.stamp = self.db_path.parent / self.STAMP

    def pull(self) -> bool:
        """The local file wins; the branch is only read to start (a new server, a lost disk)."""
        if self.db_path.exists():
            return True
        return super().pull()

    def due(self) -> bool:
        try:
            return time.time() - float(self.stamp.read_text()) >= self.every_s
        except (OSError, ValueError):
            return True

    def push(self, message: str = "Update state", extra: dict[str, bytes] | None = None,
             force: bool = False) -> None:
        for name, data in (extra or {}).items():        # the panel and later runs read them locally
            (self.db_path.parent / name).write_bytes(data)
        if not (force or self.due()):
            return
        try:
            super().push(message, extra)
        except subprocess.CalledProcessError as e:      # GitHub unreachable: retried on the next run
            print(f"backup to the {self.branch} branch failed: {e.stderr.decode(errors='replace').strip()}")
            if force:
                raise
            return
        self.stamp.write_text(str(time.time()))


def make_storage(cfg: dict, repo_dir: str | Path = "."):
    s = cfg["storage"]
    backend = os.environ.get("AGENT_STORAGE") or s["backend"]
    s2, sh = cfg.get("system2") or {}, cfg.get("shadow") or {}
    extra_dbs = [s2["db_path"]] if s2.get("enabled") else []
    if sh.get("enabled"):                       # Watch setups followed for the status panel
        extra_dbs += [sh["db_path"]] + ([sh["s2_db_path"]] if s2.get("enabled") else [])
    if backend == "local":
        return LocalStorage(s["db_path"], extra_dbs)
    if backend == "git_branch":
        return GitBranchStorage(s["db_path"], s["git_branch"], s["git_remote"], repo_dir, extra_dbs)
    if backend == "git_backup":
        return GitBackupStorage(s["db_path"], s["git_branch"], s["git_remote"], repo_dir, extra_dbs,
                                every_min=s.get("backup_min", 15))
    raise ValueError(f"unknown storage backend: {backend}")
