"""Release guard: fail if the tree or the history holds an embargoed term, a site coordinate,
or a machine-local path.

    python3 tools/release_guard.py              # every tracked file
    python3 tools/release_guard.py --history    # every added line, message and email in history

Standard library only. Prints one hit per line and exits 1 if any hit is not allowlisted.

Three checks:

- **term** — embargoed names and words. The list is stored only as SHA-256 hashes of the
  normalised term (lowercase; every run of characters outside `[a-z0-9]` becomes one space), so
  this public file does not reveal what it guards. Each line is normalised the same way and every
  1-, 2- and 3-word n-gram is hashed and looked up. A hit is reported by the first 12 hex digits
  of its hash. The plaintext list lives outside the repo with the maintainers.
- **coord** — coordinate-like pairs. A pair of 6-digit numbers with the easting in 7xxxxx and the
  northing in 3xxxxx is always a hit (a real projected-coordinate frame). Any other pair of
  6-digit numbers with decimals (`123456.7, 234567.8`) is a hit unless both values fall in a
  declared `synthetic-box`.
- **path** — machine-local home paths (`/Users/<name>`, `/home/<name>`).

`--history` scans `git log -p --all`: every added line (checked against the allowlist by the
path it was added to), every commit message, and every author and committer email.

Allowlist: `.release-guard-allow` at the repo root. Every entry needs a `# reason`.

    <path-glob>  <check>  # reason        check: term:<12-hex> | term | coord | path | *
    synthetic-box  <xmin> <ymin> <xmax> <ymax>  # reason

`<path-glob>` is matched with fnmatch against the repo-relative path. The guard does not scan
itself or the allowlist file.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import re
import subprocess
import sys
from pathlib import Path

#: SHA-256 of each normalised embargoed term (1-3 words). Never commit the plaintext.
TERM_HASHES = frozenset({
    "09b036b98677050d5568d2ab6c36463a68de35e014fbecefa12bf893226a0d3d",
    "0a26f6b7ee15e2f149d16d022a1d9efa5240d096ba5c44f4d3f1210cc8ea8f52",
    "123c616e6b6b932ed34058711dd9aac12fdf897b791f139a7d7b8eba155eedaf",
    "13fc7fcbcdf543239801e90690d531095ab4f7d29859469c149bbde237ffbe3f",
    "1b1ea3f9c3da4a23d70235838ca5ac0988f5add47fcaa14bbad74f90393abcbb",
    "1b96ba5156cce7e24086dc1ff9cc76be741d9dc3a6d87bda373e0999bdc35442",
    "1f65896b357bf188f17eb9c90f118f4a8506bf0ed568fc182f92a012890225c4",
    "23717b246edb53a4226c66a7dc36953cfa3cc7636af9ceba2d73d317d0074e71",
    "3057902be319f81e5b15e4932b9a2986ab32da2dda37ae4829816a35b7ccfe69",
    "39bfa0edbf57f1722a71798aaaac3c7250838ca15397e1cc3266140c90eac2b7",
    "43e74f2eb7c7633840b50940f48c560e2047801b4c5d14be424a6ae618d03934",
    "483a2540f0d53504744ccc66b8c99ba5e437921c3a23d32934ba33f186e153f0",
    "524722b3624d468af99fd8e152150bf0bddfa2c05d244af899b31964d2d23a2f",
    "567781541aa1d043041ec08be14e1c519629784e3952d0d079ac87640242ba5e",
    "5f505d5fc213e3787b30a2a49117bd279e236fbdc2a57804e268cd0a20f4be13",
    "62ade18b366ae07535c9b86972b22f6d68f50fed6965cb2a18646b46149ec1d2",
    "6a93f7a8baa0fa56df90c7e676b1e5ec23f2de7598eab37640428c4f06b4b8b1",
    "6c76401ebdc2d8ce12a555a666ca305a53213df581a1db1bc3c54fb5eb6c386c",
    "74518b220b9a4146b7a9a0649cbb6e1afe45e5ffa8fe7ea26d72f544b1a1177f",
    "74dd17a579debfa10466572e52ac96bf8e704f93a158c8a1883eac6bb572bab9",
    "77f041e727c01bbaa275d26a3250ba245229cdbace6e662bbf9ccc1f85d70745",
    "7a83cf6380133a968052be298a4a0a2f889f3696ffaf863b13d0799f0ceb0a37",
    "8137e03521a27c87f45971f7459250cf32b85dab738151d5947980f99d0dd99f",
    "8346e21b8f4c409693853b632ac30c016fd7231bf27fcffc6d112a2fd39f3178",
    "8495c057c5cb49abecd2f72d5fd35cabced5b8d2731fbbf9181f1ea4453f87bc",
    "8ce1249e693f6a1c59fc727e9389c1eb39d936bcec08f0e1eca84f619d5ea13f",
    "9211da2bdb79a1bf369af43968fb152c553345fe409cd7e4bc5c43f9003d6ded",
    "98d144fa7e3b819c40e8e88bf309949d1e405b3ce35a1b38fe1b435ea989b367",
    "a1b8536d7f98f6cb3c83f0abd1ff2c73eaf3e155eff6a78705f1d67a254d7fcb",
    "a587dbc0a2ea880db82e45b549a8f0564a42ebf901d87852b388cb91af77f5e6",
    "a60b9f09dfdef1b8fbe7627ea792e1e03a889569a06d8029332088b6433200ba",
    "b5881603bab7a8556a1803cd05db2a69a7bd7be260fb107d0010449ed2123d3a",
    "b80b263216edd235ad7aead7a7ac0e109609893ef4f132743bea7e9e6f44653f",
    "b9627fc2244cc9ed8a33dae1ee7fd4c4fe261a3a65464fa40fe4b9e0ae7bfe53",
    "bb29dd2f1c24e0fc17d7e579bc64e27bd09bc7715cb6e7dd1982afac2bd0abb6",
    "c6c43dad9bb5474670afdf38c6ea916b173b68f014ef3435dbbbb92820fadf61",
    "cdafc6dd7eaf85cdd6560a701ab4b374055894ab904e4feb1901b88d6b59a847",
    "cf0c7caa1cc55779e7ef489de20ba3c79aceda4fb91dd6d4ad042b23f91d2f71",
    "d2caa82cdb5a0a33f42b812083c4a4c5195805f5be2058cd9e8aec9ab6589bba",
    "e04701a0e18513432ef7f0b68d7aa65148300fec6bd72367f95cd504eed4782c",
    "e19ef6cf903ebce323462c0fc016c648f45d09dda554beb71a87a864b35e167a",
    "e25b892ee92411df7abd160acf737e433a685b2b5f4ca7354943fc7ac345032b",
    "e3d040587cd096e5752fc30429e689c1200586e45b98c2027fb2ca6cb58ed69a",
    "e491c28d24694b437c8fc31c65531c6ca8fa3f98c0fb267b707181d6f2fa21c2",
    "e52c0b475d7000ecf93216bcbfd4a0058cdfb5dba8b49a5e45f65889498e8b53",
    "e972c55ef0fae6fd36f07cddb3fbc712698f5ea8b6dfaa2fa5fcca8c78af76f3",
    "eb3b61687299145d89541b57180d246647e85a7e400e19ced9010cef12e0383d",
    "fc4f9c94394e55c0c42baa313bb1d8717a4024c671ead9b4faae1eb81ff2edba",
    "fe55b75dd9b2be54fae8571172bd3d70d65bc4ca9840c1bcda81d358299360ff",
    "ff31fe74a5dfc35e546a1ab31bf501471f1506fa5ee25dde3d5cbef683ec9dab",
})
MAX_NGRAM = 3

#: Easting 7xxxxx and northing 3xxxxx: always a hit.
UTM_PAIR = re.compile(r"(?<![\w.])7\d{5}(?:\.\d+)?[\s,;\]\[()]+3\d{5}(?:\.\d+)?(?![\w.])")
#: Any two 6-digit decimals as a pair: a hit unless inside a declared synthetic box.
DECIMAL_PAIR = re.compile(r"(?<![\w.])(\d{6}\.\d+)[\s,;]+(\d{6}\.\d+)(?![\w.])")
LOCAL_PATH = re.compile(r"/(?:users|home)/[a-z0-9._-]+", re.IGNORECASE)

ALLOW_FILE = ".release-guard-allow"
SELF = "tools/release_guard.py"
CHECKS = ("term", "coord", "path")


def term_hash(term: str) -> str:
    return hashlib.sha256(" ".join(_words(term)).encode()).hexdigest()


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


class Allow:
    """Parsed allowlist: (path-glob, check) entries and synthetic coordinate boxes."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []
        self.boxes: list[tuple[float, float, float, float]] = []

    def permits(self, path: str, check_id: str) -> bool:
        kind = check_id.split(":", 1)[0]
        return any(
            fnmatch.fnmatch(path, glob) and sel in ("*", kind, check_id)
            for glob, sel in self.entries
        )

    def in_box(self, x: float, y: float) -> bool:
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in self.boxes)


def load_allow(root: Path) -> Allow:
    allow = Allow()
    path = root / ALLOW_FILE
    if not path.exists():
        return allow
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        body, _, reason = raw.partition("#")
        if not reason.strip():
            sys.exit(f"{ALLOW_FILE}:{n}: every entry needs a '# reason'")
        parts = body.split()
        if parts and parts[0] == "synthetic-box":
            if len(parts) != 5:
                sys.exit(f"{ALLOW_FILE}:{n}: need 'synthetic-box xmin ymin xmax ymax  # reason'")
            x0, y0, x1, y1 = map(float, parts[1:])
            allow.boxes.append((x0, y0, x1, y1))
            continue
        if len(parts) != 2:
            sys.exit(f"{ALLOW_FILE}:{n}: need '<path-glob>  <check>  # reason'")
        glob, sel = parts
        kind = sel.split(":", 1)[0]
        if sel != "*" and kind not in CHECKS:
            sys.exit(f"{ALLOW_FILE}:{n}: unknown check {sel!r}")
        if kind == "term" and ":" in sel and not re.fullmatch(r"term:[0-9a-f]{12}", sel):
            sys.exit(f"{ALLOW_FILE}:{n}: a term id is 'term:' plus 12 hex digits")
        allow.entries.append((glob, sel))
    return allow


def line_hits(line: str, allow: Allow, hashes=None) -> list[str]:
    """Check ids hit by one line of text, before path allowlisting."""
    hashes = TERM_HASHES if hashes is None else hashes
    hits: list[str] = []
    words = _words(line)
    for size in range(1, MAX_NGRAM + 1):
        for i in range(len(words) - size + 1):
            digest = hashlib.sha256(" ".join(words[i:i + size]).encode()).hexdigest()
            if digest in hashes:
                hits.append(f"term:{digest[:12]}")
    if UTM_PAIR.search(line):
        hits.append("coord")
    for m in DECIMAL_PAIR.finditer(line):
        if not allow.in_box(float(m[1]), float(m[2])):
            hits.append("coord")
    if LOCAL_PATH.search(line):
        hits.append("path")
    return list(dict.fromkeys(hits))


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, check=True,
                          capture_output=True).stdout.decode("utf-8", errors="replace")


def _record(where: str, path: str, ids: list[str], allow: Allow, hits, ok) -> None:
    for check_id in ids:
        (ok if allow.permits(path, check_id) else hits).append(f"{where}:{check_id}")


def scan(root: Path, hashes=None) -> tuple[list[str], list[str]]:
    """Tree scan: (blocking hits, allowlisted hits), each as 'file:line:check'."""
    allow = load_allow(root)
    hits: list[str] = []
    ok: list[str] = []
    for rel in filter(None, _git(root, "ls-files", "-z").split("\0")):
        if rel in (SELF, ALLOW_FILE):
            continue
        p = root / rel
        if not p.is_file():
            continue
        data = p.read_bytes()
        if b"\0" in data[:8192]:
            continue                                   # binary
        text = data.decode("utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            _record(f"{rel}:{n}", rel, line_hits(rel + " " + line if n == 1 else line, allow,
                                                 hashes), allow, hits, ok)
    return hits, ok


def scan_history(root: Path, hashes=None) -> tuple[list[str], list[str]]:
    """History scan: added lines, commit messages, author and committer emails."""
    allow = load_allow(root)
    hits: list[str] = []
    ok: list[str] = []
    meta = _git(root, "log", "--all", "--format=%H%x1f%ae%x1f%ce%x1f%an%x1f%cn%x1f%B%x1e")
    for record in filter(str.strip, meta.split("\x1e")):
        sha, ae, ce, an, cn, body = record.strip("\n").split("\x1f", 5)
        short = sha[:12]
        for label, text in (("author", f"{an} {ae}"), ("committer", f"{cn} {ce}")):
            _record(f"history:{short}:{label}", "", line_hits(text, allow, hashes), allow,
                    hits, ok)
        for line in body.splitlines():
            _record(f"history:{short}:message", "", line_hits(line, allow, hashes), allow,
                    hits, ok)
    log = _git(root, "log", "-p", "--all", "--no-color", "--no-ext-diff", "--no-textconv",
               "--format=%x1fcommit %H")
    sha, path = "", ""
    for line in log.splitlines():
        if line.startswith("\x1fcommit "):
            sha, path = line.split()[1][:12], ""
        elif line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
            if path in (SELF, ALLOW_FILE):
                continue
            _record(f"history:{sha}:{path}:path", path, line_hits(path, allow, hashes), allow,
                    hits, ok)
        elif line.startswith("+") and path not in (SELF, ALLOW_FILE):
            _record(f"history:{sha}:{path}", path, line_hits(line[1:], allow, hashes), allow,
                    hits, ok)
    return list(dict.fromkeys(hits)), list(dict.fromkeys(ok))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--history", action="store_true",
                        help="scan git history (added lines, messages, emails) instead of the tree")
    args = parser.parse_args(argv)
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], check=True,
                               capture_output=True, text=True).stdout.strip())
    hits, ok = scan_history(root) if args.history else scan(root)
    for h in hits:
        print(h)
    what = "history" if args.history else "tree"
    print(f"release guard ({what}): {len(hits)} hit(s), {len(ok)} allowlisted", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
