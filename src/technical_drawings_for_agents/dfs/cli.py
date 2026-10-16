"""Commands of the reference implementation: validate, normalise, build, conformance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import furniture, resources
from .conformance import run_suite
from .pipeline import run_file
from .image import load_images
from .render_dxf import dxf_from_document
from .render_pdf import pdf_from_normalised
from .render_svg import svg_from_normalised


def _print_errors(result) -> None:
    for e in result["errors"]:
        print(f"error: clause {e['clause']}", file=sys.stderr)


def cmd_validate(args) -> int:
    result = run_file(args.document)
    if result["result"] == "error":
        _print_errors(result)
        return 1
    print("ok")
    return 0


def cmd_normalise(args) -> int:
    result = run_file(args.document)
    if result["result"] == "error":
        _print_errors(result)
        return 1
    print(json.dumps(result["normalised"], indent=2, ensure_ascii=False))
    return 0


def cmd_run(args) -> int:
    """The runner of clause 17.3: write the result JSON to standard output."""
    print(json.dumps(run_file(args.document), ensure_ascii=False, sort_keys=False))
    return 0


def cmd_seal(args) -> int:
    """Print the seals that the entries of the register should carry (clause 14.8). Writes nothing."""
    doc = json.loads(Path(args.document).read_text(encoding="utf-8"))
    entries = doc.get("revisions", {}).get("entries", [])
    for entry, seal in zip(entries, furniture.seals(entries)):
        print(f"{entry['rev']}\t{seal}")
    return 0


def cmd_build(args) -> int:
    path = Path(args.document)
    result = run_file(path)
    if result["result"] == "error":
        _print_errors(result)
        return 1
    norm = result["normalised"]
    stem = path.name[: -len(".drawing.json")] if path.name.endswith(".drawing.json") else path.stem
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    document = json.loads(path.read_text(encoding="utf-8"))
    images = load_images(document, path.resolve().parent)  # 9.10: checked by run_file, keyed by SHA-256
    dxf_text, warnings = dxf_from_document(document, norm, path.resolve().parent)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    written = {
        out / f"{stem}.normalised.json": (json.dumps(norm, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        out / f"{stem}.svg": svg_from_normalised(norm, images=images).encode("utf-8"),
        out / f"{stem}.pdf": pdf_from_normalised(norm, images),
        out / f"{stem}.dxf": dxf_text.encode("utf-8"),
    }
    for target, data in written.items():
        target.write_bytes(data)
        print(f"wrote {target}")
    return 0


def cmd_conformance(args) -> int:
    suite = Path(args.suite) if args.suite else resources.suite_dir()
    report = run_suite(suite)
    failed = [(case, problems) for case, problems in report if problems]
    for case, problems in failed:
        print(f"FAIL {case}: {problems[0]}")
    print(f"{len(report) - len(failed)} of {len(report)} cases pass")
    return 1 if failed else 0


def add_subcommands(sub) -> None:
    """Register the commands of the standard on the main parser."""
    for name, func, help_ in [
        ("validate", cmd_validate, "check a drawing document; exit 1 with the clause numbers on error"),
        ("normalise", cmd_normalise, "print the normalised content of a drawing document"),
        ("run", cmd_run, "conformance runner: print the result JSON (standard clause 17.3)"),
        ("seal", cmd_seal, "print the seals of the revision register (a person pastes them in; this writes nothing)"),
    ]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("document")
        p.set_defaults(func=func)
    p = sub.add_parser("build", help="write normalised content, SVG, PDF and DXF")
    p.add_argument("document")
    p.add_argument("--out", required=True, help="output directory")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("conformance", help="run the conformance suite against this implementation")
    p.add_argument("--suite", help="suite directory (default: the one of this checkout or wheel)")
    p.set_defaults(func=cmd_conformance)
