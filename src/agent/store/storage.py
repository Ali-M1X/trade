"""Where the SQLite file lives between runs.

LocalStorage: the file just stays on disk (VPS).
GitBranchStorage: the file is gzipped and kept as the only file on a dedicated
branch (GitHub Actions). Each push replaces the branch with a single commit so
the repository does not grow with every run.
"""
from __future__ import annotations

import gzip
import os
import subprocess
from pathlib import Path


class LocalStorage:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def pull(self) -> bool:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return self.db_path.exists()

    def push(self, message: str = "") -> None:
        pass


class GitBranchStorage:
    FILE = "state.db.gz"

    def __init__(self, db_path: str | Path, branch: str = "data", remote: str = "origin",
                 repo_dir: str | Path = "."):
        self.db_path = Path(db_path)
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
        return True

    def push(self, message: str = "Update state") -> None:
        data = gzip.compress(self.db_path.read_bytes(), mtime=0)
        blob = self._git("hash-object", "-w", "--stdin", input=data).decode().strip()
        tree = self._git("mktree", input=f"100644 blob {blob}\t{self.FILE}\n".encode()).decode().strip()
        env = {**os.environ,
               "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "trade-signal-agent"),
               "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "agent@users.noreply.github.com")}
        env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
        env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
        commit = self._git("commit-tree", tree, "-m", message, env=env).decode().strip()
        self._git("push", "--force", self.remote, f"{commit}:refs/heads/{self.branch}")


def make_storage(cfg: dict, repo_dir: str | Path = "."):
    s = cfg["storage"]
    backend = os.environ.get("AGENT_STORAGE") or s["backend"]
    if backend == "local":
        return LocalStorage(s["db_path"])
    if backend == "git_branch":
        return GitBranchStorage(s["db_path"], s["git_branch"], s["git_remote"], repo_dir)
    raise ValueError(f"unknown storage backend: {backend}")
