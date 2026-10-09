"""Shared fixtures for the ``technical_drawings_for_agents build`` tests.

Every set here is synthetic and lives under ``tmp_path``: no test touches the
vault, and none needs GDAL, QGIS, LibreOffice or ``rsvg-convert``. Where a test
needs a node that "builds" something it uses a ``run: script`` node whose script
is a few lines of Python writing a declared output.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import yaml

from technical_drawings_for_agents.cli import main

REPO = Path(__file__).resolve().parents[1]
SIMPLE = REPO / "drawings" / "example" / "simple-section"
PID_EXAMPLE = REPO / "drawings" / "example" / "synthetic-pid"

#: A copier: reads argv[1], writes argv[2]. Enough to make a real dependency
#: chain where each output's bytes depend on its input's bytes.
COPY_SCRIPT = """
import pathlib
import sys

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
print(f"wrote {dst}")
"""


def run(capsys, *argv: str) -> tuple[int, str, str]:
    """Invoke the CLI and return (exit code, stdout, stderr)."""

    code = main(["build", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def write_yaml(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def copy_script(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "copy.py"
    path.write_text(COPY_SCRIPT, encoding="utf-8")
    return path


def set_body(nodes: dict, *, set_id: str = "TEST", **extra) -> dict:
    body: dict = {"version": 1, "set": {"id": set_id}, "nodes": nodes}
    body.update(extra)
    return body


def write_set(root: Path, nodes: dict, *, name: str = "drawing-set.yaml", **extra) -> Path:
    return write_yaml(root / name, set_body(nodes, **extra))


def script_node(name: str, src: str, dst: str, *, review: str | None = None) -> dict:
    node: dict = {
        "run": "script",
        "inputs": [src],
        "outputs": [dst],
        "args": {"path": "copy.py", "argv": [src, dst]},
    }
    if review is not None:
        node["review"] = review
    return node


def abcd_set(root: Path, *, missing_b: bool = False, name: str = "drawing-set.yaml") -> Path:
    """The spec's A1 set: a -> c and b -> d, two independent chains."""

    copy_script(root)
    (root / "a.in").write_text("A", encoding="utf-8")
    if not missing_b:
        (root / "b.in").write_text("B", encoding="utf-8")
    return write_set(
        root,
        {
            "a": script_node("a", "a.in", "a.out"),
            "b": script_node("b", "b.in", "b.out"),
            "c": script_node("c", "a.out", "c.out"),
            "d": script_node("d", "b.out", "d.out"),
        },
        name=name,
    )


def diamond_nodes() -> dict:
    """a -> b, a -> c, b+c -> d: exercises the lexicographic tie-break."""

    return {
        "a": script_node("a", "a.in", "a.out"),
        "b": script_node("b", "a.out", "b.out"),
        "c": script_node("c", "a.out", "c.out"),
        "d": {
            "run": "script",
            "inputs": ["b.out", "c.out"],
            "outputs": ["d.out"],
            "args": {"path": "copy.py", "argv": ["b.out", "d.out"]},
        },
    }


def file_record(path: Path) -> tuple[int, int, str] | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


def tree_snapshot(root: Path) -> dict[str, tuple[int, int, str] | None]:
    """Every file under ``root``, by size + mtime_ns + digest.

    Used to assert that ``--check`` and ``--dry-run`` write *nothing at all*,
    including that no ``.technical_drawings_for_agents/`` directory appeared.
    """

    snapshot: dict[str, tuple[int, int, str] | None] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        snapshot[rel] = file_record(path) if path.is_file() else None
    return snapshot


def make_gpkg(path: Path, layers: dict[str, list[tuple[int, str]]]) -> Path:
    """A minimal GeoPackage-shaped SQLite file: enough tables to digest a layer."""

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS gpkg_contents ("
            "table_name TEXT PRIMARY KEY, data_type TEXT, identifier TEXT)"
        )
        for layer, rows in layers.items():
            connection.execute(f'CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY, name TEXT)')
            connection.execute(
                "INSERT INTO gpkg_contents VALUES (?, 'features', ?)", (layer, layer)
            )
            connection.executemany(f'INSERT INTO "{layer}" VALUES (?, ?)', rows)
        connection.commit()
    finally:
        connection.close()
    return path
