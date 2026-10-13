"""The release guard can fail: on a planted term, a planted coordinate and a planted history line.

The real embargo list is stored only as hashes, so these tests plant made-up terms and pass
their hashes in. Coordinates are assembled at run time so this file holds no literal pair.
"""
import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "tools" / "release_guard.py"
spec = importlib.util.spec_from_file_location("release_guard", GUARD)
rg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rg)

PLANTED = {rg.term_hash("zqx planted"), rg.term_hash("vexillum")}
TWO_WORD = "term:" + rg.term_hash("zqx planted")[:12]
ONE_WORD = "term:" + rg.term_hash("vexillum")[:12]

#: A projected-frame pair in the 7xxxxx / 3xxxxx range, built so no literal sits in this file.
REAL_RANGE_PAIR = f"{700000 + 12345}.678, {300000 + 54321}.123"
#: A generic 6-digit decimal pair outside any synthetic box.
GENERIC_PAIR = f"{200000 + 1}.5, {400000 + 2}.25"
#: A pair inside the box declared in the test allowlist.
SYNTHETIC_PAIR = f"{500000 + 600}.0, {100000 + 300}.0"

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _repo(tmp_path, files, allow=""):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    if allow:
        (tmp_path / rg.ALLOW_FILE).write_text(allow)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    return tmp_path


def _commit(root, message="c", email="test@example.com"):
    env = {**GIT_ENV, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email}
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message], check=True, env=env)


def test_the_shipped_list_is_hashes_only():
    assert rg.TERM_HASHES
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in rg.TERM_HASHES)


def test_fails_on_a_planted_term_in_any_case_and_punctuation(tmp_path):
    root = _repo(tmp_path, {"a.py": "x = 1\n# ZQX-Planted here\ny = 'Vexillum'\n"})
    hits, _ = rg.scan(root, hashes=PLANTED)
    assert hits == [f"a.py:2:{TWO_WORD}", f"a.py:3:{ONE_WORD}"]


def test_a_term_matches_whole_words_only(tmp_path):
    root = _repo(tmp_path, {"a.md": "vexillumology\nthe vexillum flies\n"})
    hits, _ = rg.scan(root, hashes=PLANTED)
    assert hits == [f"a.md:2:{ONE_WORD}"]


def test_fails_on_a_planted_coordinate(tmp_path):
    root = _repo(tmp_path, {"a.yaml": f"origin: [{REAL_RANGE_PAIR}]\nb: [{GENERIC_PAIR}]\n"})
    hits, _ = rg.scan(root, hashes=set())
    assert hits == ["a.yaml:1:coord", "a.yaml:2:coord"]


def test_an_integer_real_range_pair_is_still_a_hit(tmp_path):
    root = _repo(tmp_path, {"a.md": f"extent: [{700000 + 1}, {300000 + 2}, 9, 9]\n"})
    assert rg.scan(root, hashes=set())[0] == ["a.md:1:coord"]


def test_a_synthetic_box_allows_only_pairs_inside_it(tmp_path):
    allow = "synthetic-box  500000 100000 501000 101000  # test frame\n"
    root = _repo(tmp_path, {"a.py": f"p = ({SYNTHETIC_PAIR})\nq = ({GENERIC_PAIR})\n"},
                 allow=allow)
    hits, _ = rg.scan(root, hashes=set())
    assert hits == ["a.py:2:coord"]


def test_fails_on_a_machine_local_path(tmp_path):
    root = _repo(tmp_path, {"a.sh": "cd /" + "Users/someone/x\n"})
    assert rg.scan(root, hashes=set())[0] == ["a.sh:1:path"]


def test_allowlist_by_glob_and_check_id(tmp_path):
    allow = f"docs/*  {ONE_WORD}  # reason\n"
    root = _repo(tmp_path, {"docs/a.md": "vexillum\n", "b.md": "vexillum\n"}, allow=allow)
    hits, ok = rg.scan(root, hashes=PLANTED)
    assert hits == [f"b.md:1:{ONE_WORD}"] and ok == [f"docs/a.md:1:{ONE_WORD}"]


def test_an_allowlist_entry_without_a_reason_is_refused(tmp_path):
    root = _repo(tmp_path, {"a.md": "clean\n"}, allow="docs/*  term\n")
    with pytest.raises(SystemExit, match="reason"):
        rg.scan(root, hashes=PLANTED)


def test_untracked_files_are_not_scanned(tmp_path):
    root = _repo(tmp_path, {"a.md": "clean\n"})
    (root / "new.md").write_text("vexillum\n")
    assert rg.scan(root, hashes=PLANTED)[0] == []


def test_history_fails_on_a_line_deleted_from_the_tree(tmp_path):
    root = _repo(tmp_path, {"a.md": "clean\n"})
    _commit(root)
    (root / "a.md").write_text(f"clean\nsite at {REAL_RANGE_PAIR}\nvexillum\n")
    _commit(root)
    (root / "a.md").write_text("clean\n")
    _commit(root)

    assert rg.scan(root, hashes=PLANTED)[0] == []          # the tree is clean
    hits, _ = rg.scan_history(root, hashes=PLANTED)        # the history is not
    assert any(h.endswith(":a.md:coord") for h in hits)
    assert any(h.endswith(f":a.md:{ONE_WORD}") for h in hits)


def test_history_fails_on_a_commit_message_and_an_author_email(tmp_path):
    root = _repo(tmp_path, {"a.md": "clean\n"})
    _commit(root, message="fix the zqx planted thing", email="someone@vexillum.example")

    hits, _ = rg.scan_history(root, hashes=PLANTED)
    assert any(h.endswith(f":message:{TWO_WORD}") for h in hits)
    assert any(h.endswith(f":author:{ONE_WORD}") for h in hits)
    assert any(h.endswith(f":committer:{ONE_WORD}") for h in hits)


def test_a_clean_history_passes(tmp_path):
    root = _repo(tmp_path, {"a.md": "clean\n"})
    _commit(root)
    assert rg.scan_history(root, hashes=PLANTED) == ([], [])
