import subprocess

from agent.store.storage import GitBranchStorage, LocalStorage, make_storage


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_local_storage(tmp_path):
    s = LocalStorage(tmp_path / "d" / "state.db")
    assert s.pull() is False
    assert (tmp_path / "d").is_dir()
    s.push()


def test_git_branch_roundtrip(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    for name in ("a", "b"):
        git(tmp_path, "init", str(tmp_path / name))
        git(tmp_path / name, "remote", "add", "origin", str(remote))

    a = GitBranchStorage(tmp_path / "a" / "data" / "state.db", repo_dir=tmp_path / "a")
    assert a.pull() is False                      # branch does not exist yet
    a.db_path.write_bytes(b"sqlite-bytes-1")
    a.push("run 1")
    a.db_path.write_bytes(b"sqlite-bytes-2")
    a.push("run 2")

    b = GitBranchStorage(tmp_path / "b" / "data" / "state.db", repo_dir=tmp_path / "b")
    assert b.pull() is True
    assert b.db_path.read_bytes() == b"sqlite-bytes-2"
    # the branch is replaced, not appended to
    count = subprocess.run(["git", "rev-list", "--count", "data"], cwd=remote,
                           capture_output=True, text=True, check=True).stdout.strip()
    assert count == "1"


def test_make_storage_env_override(cfg, monkeypatch):
    assert isinstance(make_storage(cfg), LocalStorage)
    monkeypatch.setenv("AGENT_STORAGE", "git_branch")
    assert isinstance(make_storage(cfg), GitBranchStorage)
