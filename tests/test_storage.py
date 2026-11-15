import subprocess

from agent.store.storage import GitBackupStorage, GitBranchStorage, LocalStorage, make_storage


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


def test_git_branch_keeps_extra_databases(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    for name in ("a", "b"):
        git(tmp_path, "init", str(tmp_path / name))
        git(tmp_path / name, "remote", "add", "origin", str(remote))
    a = GitBranchStorage(tmp_path / "a" / "state.db", repo_dir=tmp_path / "a",
                         extra_dbs=[tmp_path / "a" / "state_s2.db"])
    a.db_path.write_bytes(b"main")
    a.push("no system 2 yet")                       # missing extra db is simply skipped
    (tmp_path / "a" / "state_s2.db").write_bytes(b"system2")
    a.push("both")
    b = GitBranchStorage(tmp_path / "b" / "state.db", repo_dir=tmp_path / "b",
                         extra_dbs=[tmp_path / "b" / "state_s2.db"])
    assert b.pull() is True
    assert (tmp_path / "b" / "state_s2.db").read_bytes() == b"system2"


def _remote(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    for name in ("a", "b"):
        git(tmp_path, "init", str(tmp_path / name))
        git(tmp_path / name, "remote", "add", "origin", str(remote))


def _branch_db(tmp_path):
    b = GitBranchStorage(tmp_path / "b" / "state.db", repo_dir=tmp_path / "b")
    return b.db_path.read_bytes() if b.pull() else None


def test_git_backup_keeps_the_local_file_and_backs_up_now_and_then(tmp_path, monkeypatch):
    _remote(tmp_path)
    seed = GitBranchStorage(tmp_path / "a" / "seed.db", repo_dir=tmp_path / "a")
    seed.FILE = "state.db.gz"
    seed.db_path.write_bytes(b"from-github")
    seed.push("github")

    s = GitBackupStorage(tmp_path / "a" / "data" / "state.db", repo_dir=tmp_path / "a", every_min=15)
    assert s.pull() is True and s.db_path.read_bytes() == b"from-github"   # a new server starts from GitHub
    s.db_path.write_bytes(b"server-1")
    assert s.pull() is True and s.db_path.read_bytes() == b"server-1"       # then the local file wins
    clock = [1_000_000.0]
    monkeypatch.setattr("agent.store.storage.time.time", lambda: clock[0])
    s.push("run", extra={"news_context.json": b"{}"})
    assert _branch_db(tmp_path) == b"server-1"                              # first push backs up
    assert (s.db_path.parent / "news_context.json").read_bytes() == b"{}"
    s.db_path.write_bytes(b"server-2")
    clock[0] += 60
    s.push("run")
    assert _branch_db(tmp_path) == b"server-1"                              # a minute later: not yet
    clock[0] += 15 * 60
    s.push("run")
    assert _branch_db(tmp_path) == b"server-2"
    s.db_path.write_bytes(b"server-3")
    s.push("backup", force=True)
    assert _branch_db(tmp_path) == b"server-3"


def test_git_backup_survives_github_being_down(tmp_path):
    git(tmp_path, "init", str(tmp_path / "a"))
    git(tmp_path / "a", "remote", "add", "origin", str(tmp_path / "missing.git"))
    s = GitBackupStorage(tmp_path / "a" / "state.db", repo_dir=tmp_path / "a")
    s.db_path.write_bytes(b"x")
    s.push("run")                                                           # no exception
    assert s.due()


def test_make_storage_git_backup(cfg, monkeypatch):
    monkeypatch.setenv("AGENT_STORAGE", "git_backup")
    s = make_storage(cfg)
    assert isinstance(s, GitBackupStorage) and s.every_s == cfg["storage"]["backup_min"] * 60
