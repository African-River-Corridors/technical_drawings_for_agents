"""``technical_drawings_for_agents`` command-line interface.

Commands:
  validate|normalise|run|build|seal|conformance <document>   drawing documents (the Drawing Format Standard)
  describe <dxf> [--expect FILE] [--json]             describe a finished DXF: layers, layouts and the
                                                      plot scale each viewport holds
  ingest <dwg-or-dxf> --out <dir>                     convert and clean a supplier DWG
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .dfs.cli import add_subcommands


def _cmd_describe(args: argparse.Namespace) -> int:
    from .describe import DescribeError, compare_description, describe_dxf

    try:
        description = describe_dxf(args.target)
    except DescribeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = description.to_dict()

    expected = None
    if args.expect:
        try:
            expected = _load_yaml_or_json(Path(args.expect))
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    findings: list[str] = []
    if expected is not None:
        findings = compare_description(
            expected, payload, scale_tolerance=args.scale_tolerance
        )

    if args.json:
        out = {"description": payload}
        if expected is not None:
            out["findings"] = findings
        _print_json(out)
    else:
        _print_describe_report(description, args.target)
        if expected is not None:
            _print_describe_findings(findings, args.expect)

    return 1 if findings else 0


def _load_yaml_or_json(path: Path):
    """Load an expectation file. YAML is a superset of JSON, so one reader serves."""
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data


def _print_describe_report(description, target: str) -> None:
    print(f"{target}")
    print(
        f"  dxf {description.dxf_version}  units {description.insunits_name}"
        f"  modelspace {description.modelspace_entities} entities"
        f"  blocks {description.block_count}"
    )
    scales = description.stated_scales
    if scales:
        print("  viewport scales: " + ", ".join(f"1:{s:g}" for s in scales))
    elif description.insunits == 0:
        # Worth saying out loud: a unitless header is why no scale is reported, and
        # it is a defect in the drawing rather than a limitation of this command.
        print("  viewport scales: NOT COMPUTABLE — header declares no units ($INSUNITS 0)")
    print(f"  layouts: {len(description.layouts)}")
    for layout in description.layouts:
        vps = ", ".join(
            f"vp{vp.viewport_id} "
            + (f"1:{vp.plot_scale:g}" if vp.plot_scale is not None else "1:?")
            + f" @{vp.paper_width_mm:g}x{vp.paper_height_mm:g}mm"
            for vp in layout.viewports
        )
        print(f"    {layout.name:<12} {layout.entities:>5} entities  {vps}")
    print(f"  layers: {len(description.layers)}")
    for name, count in description.layers:
        print(f"    {name:<24} {count:>6}")


def _print_describe_findings(findings: list[str], expect_path: str) -> None:
    if not findings:
        print(f"OK: matches {expect_path}.")
        return
    print(f"FAIL: {len(findings)} finding(s) against {expect_path}:", file=sys.stderr)
    for finding in findings:
        print(f"  - {finding}", file=sys.stderr)


def _print_json(data) -> None:
    print(json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False))


def _cmd_ingest(args: argparse.Namespace) -> int:
    from .ingest import (
        apply_corrections,
        convert_dwg,
        find_oda,
        improve,
        isolate_sheet,
        load_clean,
        strip_tags,
    )

    source = Path(args.source)
    if not source.exists():
        print(f"error: source not found: {source}", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if args.out else (source.parent / "ingest-out")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.corrections and not Path(args.corrections).exists():
        print(f"error: corrections file not found: {args.corrections}", file=sys.stderr)
        return 2

    try:
        if source.suffix.lower() == ".dwg":
            if find_oda() is None:
                print(
                    "error: ODA File Converter not found (needed to convert .dwg). "
                    "Install /Applications/ODAFileConverter.app or set ODA_CONVERTER, "
                    "or pass an already-converted .dxf.",
                    file=sys.stderr,
                )
                return 2
            dxf = convert_dwg(source, out_dir, version=args.version)
            if isinstance(dxf, list):
                dxf = dxf[0]
            print(f"Converted -> {dxf}")
        else:
            dxf = source

        doc, audit = load_clean(dxf)
        print(
            f"Loaded {dxf.name}: {audit['entities']} entities, "
            f"{audit['dimensions']} dimensions, {audit['inserts']} inserts, "
            f"{audit['text']} text, {audit['block_definitions']} block defs."
        )

        # Pipeline order: clean/improve -> strip tags -> isolate -> corrections.
        if not args.no_improve:
            imp = improve(
                doc, font=args.font, units=args.units, recolor_text_black=not args.no_recolor
            )
            print(
                f"Improved: {imp.get('styles_remapped', 0)} styles -> {args.font}, "
                f"{imp.get('text_recoloured', 0)} text recoloured, units={args.units}."
            )

        if not args.no_strip_tags:
            st = strip_tags(doc)
            print(
                f"Stripped {st['total']} template artifact(s): "
                f"{st['attdefs']} ATTDEF(s), {st['template_tags']} tag(s)."
            )

        if args.window is not None:
            box = tuple(float(v) for v in args.window.split(","))
            if len(box) != 4:
                print("error: --window needs 'xmin,ymin,xmax,ymax'", file=sys.stderr)
                return 2
            iso = isolate_sheet(doc, window=box)  # type: ignore[arg-type]
            print(f"Isolated sheet (window): kept {iso['kept']}, deleted {iso['deleted']}.")
        elif args.isolate:
            iso = isolate_sheet(doc)
            print(
                f"Isolate ({iso['mode']}): clusters={iso['clusters']}, "
                f"title-blocks={iso['title_clusters']}; kept {iso['kept']}, "
                f"deleted {iso['deleted']}."
            )

        if args.corrections:
            cor = apply_corrections(doc, args.corrections)
            print(f"Applied {cor['count']} correction(s) from {args.corrections}.")
            for a in cor["applied"]:
                print(f"  - {a}")

        cleaned = out_dir / f"{Path(dxf).stem}_clean.dxf"
        doc.saveas(cleaned)
        print(f"Clean DXF -> {cleaned}")
        print("Review it with: technical_drawings_for_agents render " + str(cleaned))
    except Exception as exc:  # noqa: BLE001
        print(f"error: ingest failed: {exc}", file=sys.stderr)
        return 1
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="technical_drawings_for_agents",
        description="Engineering drawings as data, for AI agents: one JSON drawing document gives SVG, PDF and DXF.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    add_subcommands(sub)

    p_describe = sub.add_parser(
        "describe",
        help="describe a finished DXF: layers, layouts and the plot scale each viewport holds",
    )
    p_describe.add_argument("target", help="a .dxf file")
    p_describe.add_argument(
        "--expect",
        help=(
            "YAML/JSON expectation to assert against; may be partial — only the keys "
            "present are checked. Exit 1 on any finding"
        ),
    )
    p_describe.add_argument(
        "--scale-tolerance",
        type=float,
        default=0.5,
        help="absolute tolerance in N of 1:N when comparing viewport scales (default 0.5)",
    )
    p_describe.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p_describe.set_defaults(func=_cmd_describe)


    p_ingest = sub.add_parser(
        "ingest", help="convert + clean a supplier DWG (or DXF) into an editable DXF"
    )
    p_ingest.add_argument("source", help="supplier .dwg (needs ODA) or already-converted .dxf")
    p_ingest.add_argument("--out", help="output directory (default: <source-dir>/ingest-out)")
    p_ingest.add_argument("--version", default="ACAD2018", help="ODA output DXF version")
    p_ingest.add_argument("--font", default="simhei.ttf", help="TTF to consolidate text styles to")
    p_ingest.add_argument("--units", default="mm", help="drawing units (mm/cm/m/in/ft)")
    p_ingest.add_argument("--no-improve", action="store_true", help="skip the clean-up step")
    p_ingest.add_argument("--no-recolor", action="store_true", help="keep original text colours")
    p_ingest.add_argument(
        "--no-strip-tags",
        action="store_true",
        help="keep vendor CAD-template tags (ATTDEFs, !GENTITLE-INSERT, GENST)",
    )
    p_ingest.add_argument(
        "--isolate",
        action="store_true",
        help="sheet-count-aware isolation: isolate the English sheet only when the "
        "file holds >=2 title-blocks; keep a single gapped sheet whole",
    )
    p_ingest.add_argument(
        "--window", help="isolate one sheet by explicit guard box 'xmin,ymin,xmax,ymax'"
    )
    p_ingest.add_argument(
        "--corrections", help="apply a corrections.yaml overlay (sourced value corrections)"
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
