"""``technical_drawings_for_agents`` command-line interface.

Commands:
  render <source-svg-or-py-or-dxf> --out <dir>   generate PDF/PNG for review
                                                 (LEGACY, frozen; --plot routes
                                                 it through the checked path)
  plot <source ...> [--review OUT.pdf]           THE plot path: real paper size,
                                                 fidelity-checked, or nothing
  map <plot.yaml> --out <dir>                    map sheet from GeoJSON, no QGIS
  validate <drawing-dir-or-svg> [--strict]       check title block + scale bar
                                                 + status watermark (+ meta);
                                                 --strict fails on warnings too
  layers show [--layers PATH|default]            inspect a resolved layer table
  check <drawing-dir-or-svg> [--format text|json] legibility checks (read-only)
  manifest <output> [--check] [--json]           read an output's provenance
                                                 sidecar; --check re-hashes its
                                                 declared inputs and the output
  sheet <meta-or-config.yaml> [--json|--check]   resolve a paper-space sheet:
                                                 paper size, frame, plot scale
                                                 (read-only — writes no file)
  revision list|add|seal <drawing-dir>           read, append to, or seal the
                                                 meta.yaml revision register.
                                                 There is no edit/unseal verb and
                                                 no way to set the approver.
  dimensions <dims.yaml> [--emit svg|csv|yaml|json]  dimensions + setting-out
      [--setting-out FILE] [--check-only]        computed from a placed layout;
      [--no-snap] [--warn-only]                 read-only, never a typed value
  ingest <dwg-or-dxf> --out <dir>                convert + clean a supplier DWG
      [--isolate] [--window x0,y0,x1,y1]          isolate one sheet (adaptive / explicit)
      [--corrections c.yaml] [--no-strip-tags]    apply overlay / keep template tags
  build <drawing-set.yaml> [--target N]...       build a declared drawing set in
      [--check] [--force] [--dry-run] [--timings] dependency order; --check is the
                                                 read-only "is this current?" gate
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .validate import validate_target


def _cmd_render(args: argparse.Namespace) -> int:
    from .render import render_source  # lazy: pulls ezdxf/matplotlib only when rendering

    source = Path(args.source)
    if not source.exists():
        print(f"error: source not found: {source}", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if args.out else (source.parent / "out")
    if getattr(args, "plot", False):
        # Opt-in only. Without --plot, `render` is byte-for-byte what it was.
        return _run_plot_requests([source], out_dir, _plot_defaults())
    try:
        outputs = render_source(source, out_dir, policy=_policy_from_meta(source.parent))
    except Exception as exc:  # noqa: BLE001
        print(f"error: render failed: {exc}", file=sys.stderr)
        return 1
    if not outputs:
        print(f"render ran but produced no PDF/PNG (check {out_dir} for SVG/DXF).")
        return 0
    print(f"Rendered {len(outputs)} artifact(s) into {out_dir}:")
    for path in outputs:
        print(f"  - {path}")
    return 0


def _policy_from_meta(drawing_dir: Path):
    """Return an ``EmitPolicy`` iff the drawing itself declares ``deterministic: true``.

    The declaration lives in the drawing's own ``meta.yaml``, so opting in is a
    reviewable change to the drawing, never a flag on a CI runner. Absent or
    false -> ``None`` -> today's bytes, exactly.
    """
    meta_path = drawing_dir / "meta.yaml"
    if not meta_path.exists():
        return None
    import yaml

    from .provenance import EmitPolicy, ProvenanceError

    try:
        data = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    deterministic = data.get("deterministic", False)
    if deterministic is False or deterministic is None:
        return None
    if not isinstance(deterministic, bool):
        raise ProvenanceError(
            f"{meta_path}: deterministic must be true or false, got {deterministic!r}"
        )
    return EmitPolicy.from_environment()


# --------------------------------------------------------------------------- #
# plot — THE plot path (P4). New exit codes are all >= 3, so 0/1/2 keep meaning.
# --------------------------------------------------------------------------- #


_PDF_FLAG_HELP = (
    "additionally plot the generated sheet(s) to PDF through the checked plot path "
    "(opt-in: build() return values and out/ contents are otherwise unchanged)"
)


def _plot_defaults() -> dict:
    """Argument-free plot options, for callers that only want the checked path."""
    return {}


def _plot_options(args: argparse.Namespace) -> dict:
    from .plot import PageSpec

    return {
        "page": PageSpec.parse(
            args.page, margin_mm=args.margin, landscape=not args.portrait
        ),
        "formats": tuple(dict.fromkeys(args.format or ["pdf"])),
        "fidelity": args.fidelity,
        "svg_backend": args.svg_backend,
        "white_background": not args.dark,
        "ink_check": args.ink_check,
        "dpi": args.dpi,
        # `map` shares these flags but has no --layout: a map sheet is an SVG, so
        # there is no DXF layout to choose.
        "layout": getattr(args, "layout", None),
    }


def _cmd_plot(args: argparse.Namespace) -> int:
    from .plot import PlotError, PlotRequest, discover_sheets, plot, review_bundle

    sources = [Path(item) for item in args.source]
    for source in sources:
        if not source.exists():
            print(f"error: source not found: {source}", file=sys.stderr)
            return 2
    if args.json:
        # stdout must carry EXACTLY one JSON object, so logs go to stderr at WARNING.
        logging.getLogger().setLevel(logging.WARNING)

    out_root = Path(args.out) if args.out else None
    results = []
    sheets: list[Path] = []
    bundle: Path | None = None
    try:
        options = _plot_options(args)
        for source in sources:
            if source.is_dir():
                if not args.review:
                    raise PlotError(
                        f"{source} is a directory. A directory is a plot target only with "
                        "--review, which collates sheets that already exist (it does not "
                        "build them). Name a .dxf, .svg or .py source instead.",
                        kind="input",
                    )
                review_target = Path(args.review)
                sheets.extend(
                    discover_sheets(
                        source,
                        exclude=(review_target, review_target.with_suffix(".partial.pdf")),
                    )
                )
                continue
            out_dir = out_root if out_root is not None else (source.parent / "out")
            result = plot(PlotRequest(source=source, out_dir=out_dir, **options))
            results.append(result)
            if args.review:
                sheets.extend(_bundle_candidates(result))
        if args.review:
            if not args.json:  # R5: the resolved order is inspectable without reading code
                print(f"review order ({len(sheets)} sheet(s)):")
                for index, sheet in enumerate(sheets, start=1):
                    print(f"  {index}. {sheet}")
            bundle = review_bundle(
                sheets,
                Path(args.review),
                allow_missing=args.allow_missing,
                svg_backend=args.svg_backend,
            )
    except PlotError as exc:
        _report_plot_results(results, bundle, args.json, exit_code=exc.exit_code, error=exc)
        return exc.exit_code
    _report_plot_results(results, bundle, args.json, exit_code=0, error=None)
    return 0


def _run_plot_requests(sources: list[Path], out_dir: Path, options: dict) -> int:
    """Plot each source with ``options``; used by ``render --plot`` and ``--pdf`` flags."""
    from .plot import PlotError, PlotRequest, plot

    results = []
    try:
        for source in sources:
            results.append(plot(PlotRequest(source=source, out_dir=out_dir, **options)))
    except PlotError as exc:
        _report_plot_results(results, None, False, exit_code=exc.exit_code, error=exc)
        return exc.exit_code
    _report_plot_results(results, None, False, exit_code=0, error=None)
    return 0


def _bundle_candidates(result) -> list[Path]:
    """Prefer the PDF a source produced; fall back to its SVG sheet."""
    pdfs = [path for path in result.outputs if path.suffix.lower() == ".pdf"]
    if pdfs:
        return pdfs
    return [path for path in result.outputs if path.suffix.lower() == ".svg"]


def _report_plot_results(results, bundle, as_json: bool, *, exit_code: int, error) -> None:
    if as_json:
        _print_json(
            {
                "results": [result.to_dict() for result in results],
                "bundle": None if bundle is None else str(bundle),
                "ok": exit_code == 0,
                "exit": exit_code,
                "error": None if error is None else {"kind": error.kind, "message": str(error)},
            }
        )
        if error is not None:
            print(f"error: {error}", file=sys.stderr)
        return
    states = {"ok": 0, "degraded": 0, "unchecked": 0}
    for result in results:
        states[result.state] += 1
        for path in result.outputs:
            print(
                f"PLOT {path.suffix.lstrip('.')} {path} "
                f"({result.svg_backend}, {result.pages}p, fidelity {result.state})"
            )
        if result.fidelity is not None:
            # Findings are diagnostics; the state marker above is the result.
            for finding in result.fidelity.findings:
                print(f"  {finding}", file=sys.stderr)
    if bundle is not None:
        print(f"PLOT review {bundle}")
    if results:
        total = sum(len(result.outputs) for result in results)
        print(
            f"plotted {total} artifact(s) from {len(results)} source(s); fidelity: "
            f"{states['ok']} ok, {states['degraded']} degraded, {states['unchecked']} unchecked"
        )
    if error is not None:
        print(f"error: {error}", file=sys.stderr)


def _cmd_map(args: argparse.Namespace) -> int:
    from .mapplot import build
    from .plot import PlotError

    config = Path(args.config)
    if not config.exists():
        print(f"error: map config not found: {config}", file=sys.stderr)
        return 2
    if args.json:
        logging.getLogger().setLevel(logging.WARNING)
    try:
        options = _plot_options(args)
        # A map sheet is an SVG: there is no DXF layout to pick, and F1-F4 have no
        # source entity set to compare against. Map fidelity is the per-feature
        # coverage invariant mapplot asserts before a byte is written.
        options.pop("layout")
        result = build(config, Path(args.out) if args.out else None, **options)
    except PlotError as exc:
        _report_plot_results([], None, args.json, exit_code=exc.exit_code, error=exc)
        return exc.exit_code
    if args.json:
        _print_json(
            {
                "results": [result.result.to_dict()],
                "map": result.fidelity.to_dict(),
                "svg": str(result.svg),
                "ok": True,
                "exit": 0,
            }
        )
        return 0
    print(f"MAP {result.config.number} {result.config.crs} — {result.fidelity.summary()}")
    _report_plot_results([result.result], None, False, exit_code=0, error=None)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        result = validate_target(
            args.target, layers=args.layers, legibility=args.legibility
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2
    # --strict is monotone: it can only turn warnings into problems. There is no flag
    # anywhere in this CLI that can make a failing validation pass.
    problems = list(result.problems) + (list(result.warnings) if args.strict else [])
    if not problems:
        print(f"OK: {result.target} passed validation ({len(result.checked)} checks).")
        for warning in result.warnings:
            print(f"warn: {warning}")
        return 0
    print(f"FAIL: {result.target} has {len(problems)} problem(s):", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return 1


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


def _cmd_layers_show(args: argparse.Namespace) -> int:
    from .layers import LayerTableError, resolve_layer_table

    try:
        table = resolve_layer_table(args.layers)
        assert table is not None
    except (LayerTableError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("layers:")
    print("name          aci  lw_mm  dxf_lw  linetype     plot  pen      description")
    for layer in table.layers:
        pen = table.pen(layer.name)
        print(
            f"{layer.name:<12} {layer.aci:>3}  {layer.lineweight_mm:>5g}  "
            f"{layer.dxf_lineweight:>6}  {layer.linetype:<11} "
            f"{'yes' if layer.plot else 'no':<4}  {pen.ink:<7}  {layer.description}"
        )
    if table.linetypes:
        print("linetypes:")
        for linetype in table.linetypes:
            line = (
                f"{linetype.name:<12} paper_mm=[{_fmt_pattern(linetype.pattern_mm)}]"
            )
            if args.plot_scale is not None:
                line += (
                    f" drawing_units=[{_fmt_pattern(linetype.pattern_in_units(args.plot_scale))}]"
                )
            if linetype.description:
                line += f"  {linetype.description}"
            print(line)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from .legibility import (
        CAD_PX_DEFAULTS,
        LegibilityError,
        check_drawing_dir,
        check_svg,
        gate,
        load_config,
        load_declarations,
        report_json,
    )

    target = Path(args.target)
    if not target.exists():
        print(f"error: target not found: {target}", file=sys.stderr)
        return 2
    try:
        config = load_config(args.config) if args.config else CAD_PX_DEFAULTS
        if args.require:
            from .legibility import CHECK_NAMES

            required = tuple(part.strip() for part in args.require.split(",") if part.strip())
            unknown = sorted(set(required) - CHECK_NAMES)
            if unknown:
                raise LegibilityError(f"--require names unknown check(s): {', '.join(unknown)}")
            config = _replace_legibility_config(config, require=config.require + required)
        if args.baseline:
            baseline = load_config(args.baseline).baseline
            config = _replace_legibility_config(config, baseline=config.baseline + baseline)
        if target.is_dir():
            report = check_drawing_dir(target, config=config)
        elif target.suffix.lower() == ".svg":
            declarations = None
            if args.config:
                declarations = load_declarations(args.config)
            sidecar = target.with_suffix(".legibility.json")
            if sidecar.exists():
                from .legibility import _merge_declarations  # private merge, CLI-only plumbing

                declarations = _merge_declarations(declarations, load_declarations(sidecar))
            report = check_svg(target, declarations=declarations, config=config)
        else:
            print("error: check target must be a drawing directory or .svg file", file=sys.stderr)
            return 2
    except (LegibilityError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(report_json(report, show_items=args.show_items), end="")
    else:
        _print_legibility_report(report)
    return gate(report, strict=args.strict)


def _replace_legibility_config(config, **changes):
    from dataclasses import replace

    return replace(config, **changes)


def _print_legibility_report(report) -> None:
    stream = sys.stderr if report.errors else sys.stdout
    for finding in report.findings:
        loc = "" if finding.location is None else f" at {finding.location.to_dict()}"
        print(f"{finding}{loc}", file=stream)
    if report.baselined:
        print(f"Baselined: {len(report.baselined)} finding(s)", file=sys.stdout)
    print(f"Skipped: {len(report.skipped)} check(s)", file=sys.stdout)
    for skip in report.skipped:
        print(f"  - {skip.check}: {skip.reason}", file=sys.stdout)
    status = "OK" if report.ok else "FAIL"
    print(
        f"{status}: {report.target} legibility checked "
        f"({len(report.findings)} finding(s), {len(report.checked)} checks). "
        "A clean report is not approval to issue.",
        file=stream,
    )


def _cmd_manifest(args: argparse.Namespace) -> int:
    """Read or check an output's provenance sidecar. Never writes, never approves."""
    from .provenance import Manifest, ProvenanceError

    try:
        manifest_paths = _manifest_paths(args.target)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        manifests = [Manifest.load(path) for path in manifest_paths]
    except ProvenanceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        payloads = []
        has_error = False
        for manifest in manifests:
            findings = manifest.stale_findings()
            has_error = has_error or any(finding.severity == "error" for finding in findings)
            payloads.append(_manifest_check_payload(manifest, findings))
            if not args.json:
                if not findings:
                    print(f"OK: {manifest.target_name} is current")
                for finding in findings:
                    stream = sys.stderr if finding.severity == "error" else sys.stdout
                    print(str(finding), file=stream)
        if args.json:
            _print_json(payloads[0] if len(payloads) == 1 else payloads)
        return 1 if has_error else 0

    if args.json:
        data = [manifest.to_dict() for manifest in manifests]
        _print_json(data[0] if len(data) == 1 else data)
        return 0
    for manifest in manifests:
        _print_manifest_report(manifest)
    return 0


def _manifest_paths(target: str | Path) -> list[Path]:
    from .provenance import Manifest

    target_path = Path(target)
    if target_path.is_dir():
        paths = sorted(target_path.glob("*.manifest.json"), key=lambda path: path.name)
        if not paths:
            raise FileNotFoundError(f"no manifests in {target_path}")
        return paths
    manifest_path = Manifest.path_for(target_path)
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"no manifest beside {str(target_path)!r} (expected {manifest_path})"
        )
    return [manifest_path]


def _manifest_check_payload(manifest, findings) -> dict:
    return {
        "target": manifest.target_name,
        "root_vcs": _vcs_label(manifest.root_vcs),
        "findings": [
            {"severity": f.severity, "code": f.code, "message": f.message} for f in findings
        ],
    }


def _vcs_label(vcs) -> str:
    """``unknown`` for a null dirty flag — never ``clean``, which we did not check."""
    if vcs.dirty is None:
        return "unknown"
    return "dirty" if vcs.dirty else "clean"


def _print_json(data) -> None:
    print(json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False))


def _print_manifest_report(manifest) -> None:
    print(f"target: {manifest.target_name}")
    print(f"digest: {manifest.digest}")
    print(f"stamp: {manifest.stamp_text or '-'}")
    print(f"reproducible: {_reproducible_label(manifest)}")
    print(f"inputs: {len(manifest.inputs)}")
    for record in manifest.inputs:
        print(f"  - {record.sha256[:8]} {record.path}")


def _fmt_pattern(values) -> str:
    return ", ".join(f"{float(value):g}" for value in values)


def _reproducible_label(manifest) -> str:
    if manifest.reproducible is True:
        return "yes"
    state = "no" if manifest.reproducible is False else "unmeasured"
    return f"{state} ({manifest.reason})"


def _cmd_sheet(args: argparse.Namespace) -> int:
    """Resolve a ``sheet:`` block and report it. Read-only by construction.

    There is deliberately no ``--write`` / ``--fix`` / ``--migrate``: a tool silently
    editing a drawing's own metadata record is the same category of act as a tool
    flipping its status. The author must see the change in a diff they made.
    """
    import yaml

    from .sheet import SheetError, load_sheet_config

    config = Path(args.config)
    if not config.exists():
        print(f"error: config not found: {config}", file=sys.stderr)
        return 2
    try:
        sheet = load_sheet_config(config)
    except (OSError, yaml.YAMLError) as exc:  # unusable input -> usage error
        print(f"error: cannot read {config}: {exc}", file=sys.stderr)
        return 2
    except SheetError as exc:  # a real sheet defect -> failure
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if sheet is None:
        print(f"error: {config} has no sheet: block", file=sys.stderr)
        return 2
    if args.check:
        return 0
    print(sheet.metadata_json() if args.json else _sheet_report(sheet))
    return 0


def _sheet_report(sheet) -> str:
    """The human block: every number derived, nothing typed."""
    from .sheet import sheet_metadata_json

    record = json.loads(sheet_metadata_json(sheet))
    vp = sheet.viewport
    lines = [
        f"paper:            {sheet.paper.name} {sheet.paper.orientation} "
        f"{sheet.paper.width_mm:g} × {sheet.paper.height_mm:g} mm",
        f"margins_mm:       left {sheet.margins.left_mm:g}, right {sheet.margins.right_mm:g}, "
        f"top {sheet.margins.top_mm:g}, bottom {sheet.margins.bottom_mm:g}"
        "   (house default 10 mm — not an ISO 5457 citation)",
        f"drawing frame:    x {sheet.frame.x_mm:g}, y {sheet.frame.y_mm:g}, "
        f"{sheet.frame.width_mm:g} × {sheet.frame.height_mm:g} mm",
    ]
    if sheet.regions:
        lines.append("reservations:")
        for name, frame in sheet.regions.items():
            lines.append(
                f"  - {name}: x {frame.x_mm:g}, y {frame.y_mm:g}, "
                f"{frame.width_mm:g} × {frame.height_mm:g} mm"
            )
    lines.extend(
        [
            f"viewport frame:   x {vp.frame.x_mm:g}, y {vp.frame.y_mm:g}, "
            f"{vp.frame.width_mm:g} × {vp.frame.height_mm:g} mm",
            f"extent_m:         [{', '.join(f'{value:g}' for value in vp.extent_m)}]",
            f"rotation_deg:     {vp.rotation_deg:g}",
            f"required_mm:      {vp.required_mm[0]:.3f} × {vp.required_mm[1]:.3f}"
            f"   (fits: {vp.fits})",
            f"scale:            {sheet.scale_text}"
            f"   (ISO 5455 preferred: {vp.scale.is_preferred})",
            f"mm per model m:   {vp.scale.mm_per_m():.6g}",
            f"scale bar:        {record['scale_bar']['length_m']:g} m = "
            f"{record['scale_bar']['total_mm']:.3f} mm, "
            f"{sheet.scale_bar.divisions} divisions, unit {sheet.scale_bar.unit}",
        ]
    )
    if sheet.north is not None:
        lines.append(
            f"north:            {record['north']['paper_deg']:.3f}° on paper "
            f"(model bearing {sheet.north.model_bearing_deg:g}°, label "
            f"{sheet.north.label!r})"
        )
    lines.extend(f"warn: {warning}" for warning in sheet.warnings)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# revision register (P9 / #61)
#
# Three verbs: list, add, seal. Note what is absent and must stay absent —
# there is no `revision edit`, no `revision unseal`, no `--app`, no `--approve`,
# no `--sign`, no `--issue` and no `--force`. Correcting a sealed row means a
# human editing YAML by hand and consciously re-sealing, which leaves a diff in
# git with a commit author on it. A test introspects this parser to keep it so.
# --------------------------------------------------------------------------- #


def _cmd_revision_list(args: argparse.Namespace) -> int:
    from .meta import DrawingMeta

    meta_path = _meta_path(args.drawing_dir)
    if not meta_path.exists():
        print(f"error: meta.yaml not found: {meta_path}", file=sys.stderr)
        return 2
    try:
        meta = DrawingMeta.load(meta_path)
        register = meta.register()
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if register is None:
        print(f"{meta_path}: no revisions: register")
        return 0
    print(f"{meta_path}: {len(register.entries)} revision(s), scheme {register.scheme}")
    print("rev  date        by    chk   app   seal    description")
    for entry in register.entries:
        seal = "sealed" if entry.is_sealed else "-"
        print(
            f"{entry.rev:<4} {entry.date.isoformat()}  {entry.by:<5} "
            f"{entry.chk or '-':<5} {entry.app or '-':<5} {seal:<7} {entry.description}"
        )
    return 0


def _cmd_revision_add(args: argparse.Namespace) -> int:
    """Append a revision row and bump ``meta.revision`` to match.

    Writes ``revisions`` and ``revision`` and nothing else: never ``status``,
    never ``for_construction``, never ``app``. The appended entry is constructed
    with ``app=None`` and there is no flag that could set it.
    """
    import yaml

    from .meta import DrawingMeta
    from .revisions import Revision

    meta_path = _meta_path(args.drawing_dir)
    if not meta_path.exists():
        print(f"error: meta.yaml not found: {meta_path}", file=sys.stderr)
        return 2
    try:
        original = meta_path.read_text(encoding="utf-8")
        data = yaml.safe_load(original) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{meta_path}: top level must be a mapping")
        entry = Revision(
            rev=args.rev,
            date=_revision_date(args.date),
            description=args.description,
            by=args.by,
            chk=args.chk,
            app=None,
        )
        meta = DrawingMeta.from_dict(data)
        register = meta.register()
        if register is not None:
            # Raises when the token duplicates, goes backwards, or would
            # invalidate a sealed prefix.
            register.append(entry)
        rewritten = _rewrite_revision_add(original, entry)
        _write_meta(meta_path, rewritten)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"added revision {entry.rev} to {meta_path}")
    return 0


def _cmd_revision_seal(args: argparse.Namespace) -> int:
    """Seal one already-approved ISSUED row. A human act, performed at a terminal.

    Refuses unless *all* of: ``CI`` is unset, ``stdin`` is a TTY, the drawing's
    ``status`` is ISSUED, and the entry's ``app`` was **already** a non-empty value
    in the file before this command ran. A signature CI can apply is not a
    signature, so the environment checks come first and nothing is written before
    the operator retypes the revision token.
    """
    import yaml

    from .meta import DrawingMeta

    meta_path = _meta_path(args.drawing_dir)
    if os.environ.get("CI"):
        print(
            "error: revision seal refuses to run under CI — sealing an issued "
            "revision is a human act performed at a terminal (CI is set)",
            file=sys.stderr,
        )
        return 2
    if not sys.stdin.isatty():
        print(
            "error: revision seal requires an interactive terminal (stdin is not a tty)",
            file=sys.stderr,
        )
        return 2
    if not meta_path.exists():
        print(f"error: meta.yaml not found: {meta_path}", file=sys.stderr)
        return 2

    try:
        original = meta_path.read_text(encoding="utf-8")
        data = yaml.safe_load(original) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{meta_path}: top level must be a mapping")
        meta = DrawingMeta.from_dict(data)
        if meta.status_key != "ISSUED":
            raise ValueError(
                f"{meta_path}: revision seal requires status: ISSUED, found "
                f"{meta.status_key or '(empty)'}"
            )
        register = meta.register()
        if register is None:
            raise ValueError(f"{meta_path}: revision seal requires a revisions: register")
        entry = register.get(args.rev)
        if entry is None:
            raise ValueError(
                f"{meta_path}: revision {args.rev!r} is absent from revisions:"
            )
        if not entry.is_approved:
            raise ValueError(
                f"{meta_path}: revisions[].app is empty for rev {entry.rev} — a named "
                "approver must already be in the file; no tool may supply it"
            )
        print(f"About to seal revision {entry.rev} of {meta_path}:")
        print(f"  date:        {entry.date.isoformat()}")
        print(f"  description: {entry.description}")
        print(f"  by:          {entry.by}")
        print(f"  app:         {entry.app}")
        if input(f"Type {entry.rev} to seal: ").strip().upper() != entry.rev:
            raise ValueError("confirmation did not match the revision token; nothing written")
        seal = _seal_for(register, entry.rev)
        _write_meta(meta_path, _rewrite_revision_seal(original, entry.rev, seal))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"sealed revision {entry.rev}")
    return 0


def _meta_path(drawing_dir: str | Path) -> Path:
    return Path(drawing_dir) / "meta.yaml"


def _write_meta(path: Path, text: str) -> None:
    """Write ``meta.yaml`` through P3's canonical writer: UTF-8, LF, no BOM, atomic."""
    from .provenance import EmitPolicy, write_text_canonical

    write_text_canonical(path, text, EmitPolicy())


def _revision_date(raw: str | None) -> datetime.date:
    if raw is None:
        return datetime.date.today()
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"--date must be YYYY-MM-DD, got {raw!r}") from exc


def _rewrite_revision_add(text: str, entry) -> str:
    """Append one entry to ``revisions:`` and set ``revision:`` — a textual edit.

    Textual rather than a YAML round-trip on purpose: ``yaml.safe_dump`` would
    rewrite the whole file, losing the comments that carry the lifecycle and
    stable-ID rules, and turning a one-row append into an unreviewable diff.
    """
    lines = text.splitlines(keepends=True)
    lines = _set_top_level_scalar(lines, "revision", entry.rev)
    start = _top_level_key_index(lines, "revisions")
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.extend(["revisions:\n", *_revision_entry_lines(entry, "  ", "    ")])
        return "".join(lines)
    if lines[start].strip() == "revisions: []":
        lines[start] = "revisions:\n"
        lines[start + 1 : start + 1] = _revision_entry_lines(entry, "  ", "    ")
        return "".join(lines)
    dash_indent, field_indent = _revision_indents(lines, start)
    insert_at = _top_level_block_end(lines, start)
    lines[insert_at:insert_at] = _revision_entry_lines(entry, dash_indent, field_indent)
    return "".join(lines)


def _revision_indents(lines: list[str], start: int) -> tuple[str, str]:
    """The indentation an existing ``revisions:`` block already uses.

    ``yaml.safe_dump`` writes sequence items at column 0 under their key while a
    hand-authored file usually indents them by two. Matching whichever the file
    already uses keeps the append a one-block diff instead of a reformat.
    """
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if _is_top_level(line):
            break
        stripped = line.lstrip()
        if stripped.startswith("- "):
            dash = line[: len(line) - len(stripped)]
            return dash, dash + "  "
        index += 1
    return "  ", "    "


def _revision_entry_lines(entry, dash_indent: str, field_indent: str) -> list[str]:
    return [
        f"{dash_indent}- rev: {entry.rev}\n",
        f"{field_indent}date: {entry.date.isoformat()}\n",
        f"{field_indent}description: {_yaml_string(entry.description)}\n",
        f"{field_indent}by: {_yaml_string(entry.by)}\n",
        f"{field_indent}chk: {_yaml_string(entry.chk)}\n" if entry.chk else f"{field_indent}chk:\n",
        # Written empty, always. The approver is a human's signature, and the cell
        # is emitted so it is visible as deliberately blank rather than missing.
        f"{field_indent}app:\n",
    ]


def _rewrite_revision_seal(text: str, rev: str, seal: str) -> str:
    lines = text.splitlines(keepends=True)
    start = _top_level_key_index(lines, "revisions")
    if start is None:
        raise ValueError("revisions: block not found")
    _dash_indent, field_indent = _revision_indents(lines, start)
    index = start + 1
    target = str(rev).strip().upper()
    while index < len(lines):
        if _is_top_level(lines[index]):
            break
        stripped = lines[index].strip()
        if stripped.startswith("- rev:") and _rev_from_line(stripped) == target:
            entry_end = _revision_entry_end(lines, index)
            for seal_index in range(index + 1, entry_end):
                line = lines[seal_index]
                if line.lstrip().startswith("seal:"):
                    indent = line[: len(line) - len(line.lstrip())]
                    lines[seal_index] = f"{indent}seal: {seal}\n"
                    return "".join(lines)
            lines[entry_end:entry_end] = [f"{field_indent}seal: {seal}\n"]
            return "".join(lines)
        index += 1
    raise ValueError(f"could not locate revision {rev!r} in the revisions: block")


def _set_top_level_scalar(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{key}: {value}\n"
            return lines
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{key}: {value}\n")
    return lines


def _top_level_key_index(lines: list[str], key: str) -> int | None:
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            return index
    return None


def _top_level_block_end(lines: list[str], start: int) -> int:
    index = start + 1
    while index < len(lines):
        if _is_top_level(lines[index]):
            return index
        index += 1
    return index


def _revision_entry_end(lines: list[str], start: int) -> int:
    index = start + 1
    while index < len(lines):
        if lines[index].strip().startswith("- rev:") or _is_top_level(lines[index]):
            return index
        index += 1
    return index


def _is_top_level(line: str) -> bool:
    """True for a top-level mapping key.

    A block-sequence item is **not** top level even when ``yaml.safe_dump`` writes
    its dash at column 0: it still belongs to the key above it. Getting this wrong
    makes an append land in the middle of the register.
    """
    stripped = line.strip()
    if not stripped or line.startswith((" ", "\t", "#")):
        return False
    return not stripped.startswith("- ")


def _rev_from_line(stripped: str) -> str:
    _, _, value = stripped.partition(":")
    return value.strip().strip("'\"").upper()


def _yaml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _seal_for(register, rev: str) -> str:
    """The chained digest for one entry — its fields, its index, and its prefix."""
    target = str(rev).strip().upper()
    for entry, digest in zip(register.entries, register.digests()):
        if entry.rev == target:
            return digest
    raise ValueError(f"revision {rev!r} is absent from revisions:")


def _cmd_build(args: argparse.Namespace) -> int:
    """Build a declared drawing set. The graph decides the order; nobody remembers it."""
    from .build import (  # lazy, per house style
        BuildError,
        JsonStateStore,
        build_set,
        load_drawing_set,
        plan,
        required_tool_failures,
        review_failures,
        review_targets,
        select,
        size_mtime_inputs,
        state_version_notes,
    )

    # --force asserts staleness, --check measures it; --dry-run explains. Two of
    # the three together would need one exit-code convention for two questions.
    if args.check and args.force:
        print("error: --check and --force are mutually exclusive", file=sys.stderr)
        return 2
    if args.check and args.dry_run:
        print("error: --check and --dry-run are mutually exclusive", file=sys.stderr)
        return 2

    try:
        dset = load_drawing_set(args.drawing_set)
        selected = select(dset, args.target or None)
        store = JsonStateStore(dset.root)
        statuses = plan(dset, targets=selected, force=args.force, state=store)
    except BuildError as exc:
        for line in str(exc).splitlines():
            print(f"error: {line}", file=sys.stderr)
        return 2

    notes: list[str] = []
    if args.force:
        external = sorted(
            status.node for status in statuses if dset.nodes[status.node].run == "external"
        )
        if external:
            notes.append(f"--force does not run external node(s): {', '.join(external)}")
    size_mtime = size_mtime_inputs(dset)
    if size_mtime:
        notes.append(
            f"{len(size_mtime)} input(s) tracked by size+mtime, not content: "
            f"{', '.join(size_mtime)}"
        )
    # Correction 3: adoption is a state change the operator should see.
    for status in statuses:
        if status.adopt:
            notes.append(f"external node '{status.node}': {status.reason}")
    notes.extend(state_version_notes(dset, statuses, store))

    reviewed = review_targets(dset, selected)

    if args.dry_run:
        _print_build_header(dset, len(selected), mode="")
        _print_notes(dset.notes, notes)
        _print_plan_lines(statuses)
        _print_review_plan(reviewed, review_failures(dset, selected))
        return 0

    if args.check:
        from .build import CHECK_RUNNERS

        missing_tools = required_tool_failures(dset, statuses)
        _print_build_header(dset, len(selected), mode=" --check")
        _print_notes(dset.notes, notes)
        _print_check_lines(dset, statuses, missing_tools)
        # Correction 2: --check verifies every declared review target's PDF is
        # present and current, and writes nothing at all doing it.
        pdf_gaps = review_failures(dset, selected)
        for node, label in pdf_gaps:
            print(f"{'CHECK':>7}  {node}  NO PDF: review: pdf declared but absent: {label}")
        # A check node has no artifact, so it is neither current nor stale. Counting
        # it as stale would mean no set containing one could ever pass this gate.
        stale = sum(
            1
            for status in statuses
            if status.state in {"stale", "unbuilt", "missing-output"}
            and dset.nodes[status.node].run not in CHECK_RUNNERS
        )
        blocked = sum(1 for status in statuses if status.state == "blocked")
        if stale or blocked or missing_tools or pdf_gaps:
            sys.stdout.flush()  # keep the report above its verdict when piped
            print(
                f"check: NOT CURRENT - {stale} stale, {blocked} blocked, "
                f"{len(missing_tools)} missing tool(s), {len(pdf_gaps)} missing review PDF(s)",
                file=sys.stderr,
            )
            return 3
        print(f"check: current ({len(selected)} node(s))")
        return 0

    try:
        # build_set recomputes the plan; both passes are read-only until pre-flight
        # has passed, which keeps the API free of the CLI's printing.
        report = build_set(dset, targets=selected, force=args.force, state=store)
    except BuildError as exc:
        for line in str(exc).splitlines():
            print(f"error: {line}", file=sys.stderr)
        return 2

    _print_build_header(dset, len(selected), mode="")
    _print_notes(dset.notes, notes)
    _print_build_report(dset, statuses, report, timings=args.timings)
    return 0 if report.ok else 1


def _print_build_header(dset, selected: int, *, mode: str) -> None:
    print(f"technical_drawings_for_agents build{mode} {dset.id}: {len(dset.nodes)} node(s), {selected} selected")


def _print_notes(raw_notes, notes: list[str]) -> None:
    for note in raw_notes:
        print(note)
    for note in notes:
        print(note if note.startswith("note: ") else f"note: {note}")


def _status_token(state: str) -> str:
    return {
        "current": "SKIP",
        "stale": "BUILD",
        "unbuilt": "BUILD",
        "missing-output": "BUILD",
        "blocked": "BLOCKED",
    }[state]


def _node_width(statuses) -> int:
    return max((len(status.node) for status in statuses), default=0)


def _print_plan_lines(statuses) -> None:
    """``--dry-run`` shows the actions a build would take, not a verdict."""
    width = _node_width(statuses)
    for status in statuses:
        print(f"{_status_token(status.state):>7}  {status.node:<{width}}  {status.reason}")


def _print_review_plan(reviewed, pdf_gaps) -> None:
    if not reviewed:
        return
    gaps = {node for node, _ in pdf_gaps}
    for node in reviewed:
        state = "PDF absent" if node in gaps else "PDF present"
        print(f"note: review target '{node}': {state}")


def _print_check_lines(dset, statuses, missing_tools) -> None:
    from .build import CHECK_RUNNERS

    width = _node_width(statuses)
    missing_by_node: dict[str, list[str]] = {}
    for node, tool in missing_tools:
        missing_by_node.setdefault(node, []).append(tool)
    for status in statuses:
        if status.node in missing_by_node:
            tools = ", ".join(sorted(missing_by_node[status.node]))
            print(f"{'CHECK':>7}  {status.node:<{width}}  MISSING TOOL: {tools}")
            continue
        if dset.nodes[status.node].run in CHECK_RUNNERS:
            # Named "would run", never "current": --check runs nothing, so it must
            # not be readable as "this validation passed".
            print(f"{'CHECK':>7}  {status.node:<{width}}  WOULD RUN: {status.reason}")
            continue
        print(f"{'CHECK':>7}  {status.node:<{width}}  {_check_reason(status)}")


def _check_reason(status) -> str:
    if status.state == "current":
        return "current"
    if status.state == "blocked":
        return f"BLOCKED: {status.reason}"
    if status.state == "missing-output":
        return f"MISSING: {status.reason}"
    return f"STALE: {status.reason}"


def _print_build_report(dset, statuses, report, *, timings: bool) -> None:
    status_by_node = {status.node: status for status in statuses}
    width = _node_width(statuses)
    built = skipped = blocked = failed = 0
    for result in report.results:
        status = status_by_node[result.node]
        if result.action == "skipped":
            skipped += 1
            print(f"{'SKIP':>7}  {result.node:<{width}}  {status.reason}")
        elif result.action == "blocked":
            blocked += 1
            print(f"{'BLOCKED':>7}  {result.node:<{width}}  {status.reason}")
        elif result.action == "built":
            built += 1
            print(f"{'BUILD':>7}  {result.node:<{width}}  {status.reason}")
            _print_captured(result.stdout, stream=sys.stdout)
            _print_captured(result.stderr, stream=sys.stderr)
            labels = ", ".join(_output_labels(dset, result.node)) or "(no artifact)"
            suffix = (
                f"  ({result.duration_s:.1f}s)"
                if timings and result.duration_s is not None
                else ""
            )
            print(f"{'OK':>7}  {result.node:<{width}}  -> {labels}{suffix}")
        else:
            failed += 1
            print(f"{'BUILD':>7}  {result.node:<{width}}  {status.reason}")
            sys.stdout.flush()  # the failure report belongs under its BUILD line
            print(f"{'FAIL':>7}  {result.node:<{width}}  {result.error}", file=sys.stderr)
            _print_captured(result.stdout, stream=sys.stderr)
            _print_captured(result.stderr, stream=sys.stderr)
            for original, partial in result.quarantined:
                print(
                    f"  quarantined: {_set_rel(dset, original)} -> {partial.name}",
                    file=sys.stderr,
                )
            print(f"error: node '{result.node}' failed; 0 further node(s) run", file=sys.stderr)
    for node, label in report.review_failures:
        print(
            f"error: node '{node}': review: pdf declared but no PDF was produced: {label}",
            file=sys.stderr,
        )
    if report.stranded:
        print(
            f"note: {len(report.stranded)} node(s) became stale and were not selected: "
            f"{', '.join(report.stranded)}"
        )
    print(f"build: {built} built, {skipped} skipped, {blocked} blocked, {failed} failed")


def _print_captured(text: str, *, stream) -> None:
    for line in text.splitlines():
        print(f"    {line}", file=stream)


def _output_labels(dset, node_name: str) -> list[str]:
    labels = []
    for ref in dset.nodes[node_name].output_refs:
        label = _set_rel(dset, ref.path)
        labels.append(f"{label}:{ref.layer}" if ref.layer is not None else label)
    return sorted(labels)


def _set_rel(dset, path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(dset.root).as_posix()
    except ValueError:
        return resolved.as_posix()


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


def _add_plot_flags(parser: argparse.ArgumentParser) -> None:
    """The plot options shared by ``plot`` and ``map``. One definition, two verbs."""
    parser.add_argument("--page", default="ISO A3", help="'ISO A1' | 'ANSI D' | '420x297'")
    parser.add_argument("--portrait", action="store_true", help="default is landscape")
    parser.add_argument("--margin", type=float, default=10.0, help="sheet margin in mm")
    parser.add_argument(
        "--format",
        action="append",
        choices=["pdf", "png", "svg"],
        help="repeatable output format (default: pdf)",
    )
    parser.add_argument("--dpi", type=int, default=300, help="PNG resolution")
    parser.add_argument(
        "--fidelity",
        choices=["strict", "warn", "off"],
        default="strict",
        help="strict (default) refuses to write a canonical artifact for an unfaithful "
        "plot; warn writes '<stem>.degraded.pdf'; off writes '<stem>.unchecked.pdf'",
    )
    parser.add_argument(
        "--svg-backend", choices=["auto", "rsvg", "cairosvg"], default="auto",
        help="SVG->PDF converter (auto prefers rsvg-convert)",
    )
    parser.add_argument(
        "--dark", action="store_true", help="keep ezdxf's dark sheet (default: white)"
    )
    parser.add_argument(
        "--ink-check",
        action="store_true",
        help="additionally rasterise the PDF and require ink on every page (needs "
        "pdftoppm + Pillow)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit exactly one JSON object on stdout"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="technical_drawings_for_agents",
        description="Engineering-drawings-as-code: render and validate drawing sheets.",
    )
    parser.add_argument("--version", action="version", version=f"technical_drawings_for_agents {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser(
        "render", help="render a source to PDF/PNG for review (legacy path, frozen)"
    )
    p_render.add_argument("source", help="source .py generator, .svg, or .dxf")
    p_render.add_argument("--out", help="output directory (default: <source-dir>/out)")
    p_render.add_argument(
        "--plot",
        action="store_true",
        help="route through the checked plot path instead (real paper size, fidelity "
        "check, no LibreOffice). Opt-in: without it, render output is unchanged",
    )
    p_render.set_defaults(func=_cmd_render)

    p_plot = sub.add_parser(
        "plot",
        help="plot a source to a review-grade PDF: real paper size, fidelity-checked",
        description="Every artifact is either complete or absent. '<stem>.pdf' means it "
        "passed every check; a degraded artifact can only ever land as '<stem>.degraded.pdf'.",
    )
    p_plot.add_argument(
        "source", nargs="+", help="one or more .dxf / .svg / .py sources, or dirs with --review"
    )
    p_plot.add_argument("--out", help="output directory (default: <first-source-dir>/out)")
    _add_plot_flags(p_plot)
    p_plot.add_argument("--layout", help="DXF layout to plot (default: auto-detect)")
    p_plot.add_argument(
        "--review", metavar="OUT.pdf", help="bundle the sheets into one multi-page PDF"
    )
    p_plot.add_argument(
        "--allow-missing",
        action="store_true",
        help="write an INCOMPLETE bundle as <name>.partial.pdf with placeholder pages; "
        "still exits 4",
    )
    p_plot.set_defaults(func=_cmd_plot)

    p_map = sub.add_parser(
        "map", help="plot a map sheet from GeoJSON — no QGIS, no GDAL, no reprojection"
    )
    p_map.add_argument("config", help="map plot.yaml (map.crs / map.extent / map.layers / meta)")
    p_map.add_argument("--out", help="output directory (default: <config-dir>/out)")
    _add_plot_flags(p_map)
    p_map.set_defaults(func=_cmd_map)

    p_validate = sub.add_parser(
        "validate", help="check a drawing has a title block + scale bar + status watermark"
    )
    p_validate.add_argument("target", help="drawing directory or .svg file")
    p_validate.add_argument(
        "--strict",
        action="store_true",
        help="promote validation warnings to problems (exit 1)",
    )
    p_validate.add_argument(
        "--layers",
        help="validate DXF layers against PATH, or 'default' for the packaged layer table",
    )
    p_validate.add_argument(
        "--legibility",
        action="store_true",
        help="also run opt-in legibility checks on emitted SVG artifacts",
    )
    p_validate.set_defaults(func=_cmd_validate)

    p_layers = sub.add_parser("layers", help="inspect layer tables")
    layers_sub = p_layers.add_subparsers(dest="layers_command", required=True)
    p_layers_show = layers_sub.add_parser("show", help="show a resolved layer table")
    p_layers_show.add_argument(
        "--layers",
        default="default",
        help="layer table path, or 'default' for the packaged table",
    )
    p_layers_show.add_argument(
        "--plot-scale",
        type=float,
        help="show custom linetype patterns converted for a 1:N plot scale",
    )
    p_layers_show.set_defaults(func=_cmd_layers_show)

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

    p_check = sub.add_parser("check", help="run deterministic legibility checks")
    p_check.add_argument("target", help="drawing directory or .svg file")
    p_check.add_argument("--config", help="legibility.yaml with config and/or declarations")
    p_check.add_argument(
        "--strict",
        action="store_true",
        help="promote warnings and skipped checks to failure (exit 1)",
    )
    p_check.add_argument(
        "--require",
        help="comma-separated checks whose skip is an error",
    )
    p_check.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="output format",
    )
    p_check.add_argument("--baseline", help="YAML file containing baseline entries")
    p_check.add_argument(
        "--show-items",
        action="store_true",
        help="include parsed items in --format json output",
    )
    p_check.set_defaults(func=_cmd_check)

    p_manifest = sub.add_parser("manifest", help="inspect or check an output manifest")
    p_manifest.add_argument("target", help="output file, or a directory of manifested outputs")
    p_manifest.add_argument(
        "--check",
        action="store_true",
        help="re-hash the declared inputs and the output; exit 1 on any error finding",
    )
    p_manifest.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p_manifest.set_defaults(func=_cmd_manifest)

    p_sheet = sub.add_parser(
        "sheet",
        help="resolve a paper-space sheet: binding, plot scale and frame (read-only)",
    )
    p_sheet.add_argument("config", help="meta.yaml (or any YAML) carrying a sheet: block")
    p_sheet.add_argument(
        "--json", action="store_true", help="print the resolved sheet record as JSON"
    )
    p_sheet.add_argument(
        "--check", action="store_true", help="resolve only: no output on success (CI)"
    )
    p_sheet.set_defaults(func=_cmd_sheet)

    p_revision = sub.add_parser(
        "revision", help="list, append or seal entries in a meta.yaml revisions: register"
    )
    revision_sub = p_revision.add_subparsers(dest="revision_command", required=True)

    p_revision_list = revision_sub.add_parser("list", help="show the revision register")
    p_revision_list.add_argument("drawing_dir", help="drawing directory containing meta.yaml")
    p_revision_list.set_defaults(func=_cmd_revision_list)

    p_revision_add = revision_sub.add_parser(
        "add", help="append a revision row and bump meta.revision to match"
    )
    p_revision_add.add_argument("drawing_dir", help="drawing directory containing meta.yaml")
    p_revision_add.add_argument("--rev", required=True, help="revision token to append")
    p_revision_add.add_argument(
        "--description", required=True, help="what changed in this revision"
    )
    p_revision_add.add_argument("--by", required=True, help="drafter/author initials")
    p_revision_add.add_argument("--chk", help="checker initials")
    p_revision_add.add_argument("--date", help="issue date, YYYY-MM-DD (default: today)")
    # No --app. The approver is the responsible engineer's signature; it is only
    # ever a value a human typed into meta.yaml. See test_revision_add_has_no_app_flag.
    p_revision_add.set_defaults(func=_cmd_revision_add)

    p_revision_seal = revision_sub.add_parser(
        "seal", help="seal an already-approved ISSUED row (interactive terminal only)"
    )
    p_revision_seal.add_argument("drawing_dir", help="drawing directory containing meta.yaml")
    p_revision_seal.add_argument("--rev", required=True, help="revision token to seal")
    p_revision_seal.set_defaults(func=_cmd_revision_seal)

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

    p_bfd = sub.add_parser(
        "bfd", help="generate a block-flow schematic / hydraulic profile from a data YAML"
    )
    p_bfd.add_argument("data", help="drawing data YAML (nodes/edges/groups/meta)")
    p_bfd.add_argument("--out", help="output directory (default: <data-dir>/out)")
    p_bfd.add_argument(
        "--view", default="block", choices=["block", "swimlane", "profile", "all"],
        help="block-flow schematic, hydraulic long-section, or both",
    )
    p_bfd.add_argument("--pdf", action="store_true", help=_PDF_FLAG_HELP)
    p_bfd.set_defaults(func=_cmd_bfd)

    p_pid = sub.add_parser(
        "pid", help="generate an ISA-5.1 P&ID from a data YAML (positional layout)"
    )
    p_pid.add_argument("data", help="P&ID data YAML (equipment/instruments/lines/loops/meta)")
    p_pid.add_argument("--out", help="output directory (default: <data-dir>/out)")
    p_pid.add_argument("--pdf", action="store_true", help=_PDF_FLAG_HELP)
    p_pid.set_defaults(func=_cmd_pid)

    p_build = sub.add_parser(
        "build",
        help="build a declarative drawing set in dependency order (no human build order)",
    )
    p_build.add_argument("drawing_set", help="drawing-set.yaml")
    p_build.add_argument(
        "--target",
        action="append",
        metavar="NAME",
        help="target or node to build, plus its transitive dependencies (repeatable)",
    )
    p_build.add_argument(
        "--check",
        action="store_true",
        help="report whether the set is current and buildable; writes nothing, runs nothing",
    )
    p_build.add_argument(
        "--force",
        action="store_true",
        help="treat every selected non-external node as stale (never runs an external node)",
    )
    p_build.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and exit 0; runs nothing, writes nothing",
    )
    p_build.add_argument(
        "--timings",
        action="store_true",
        help="append a duration to each OK line (off by default so stdout is diffable)",
    )
    p_build.set_defaults(func=_cmd_build)

    from .components.cli import (
        add_dimensions_parser,
        add_layout_parser,
    )
    from .components.cli import (
        add_parser as add_component_parser,
    )

    add_component_parser(sub)
    add_layout_parser(sub)
    add_dimensions_parser(sub)

    return parser


def _cmd_pid(args: argparse.Namespace) -> int:
    from . import pid

    data = Path(args.data)
    if not data.exists():
        print(f"error: data not found: {data}", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if args.out else (data.parent / "out")
    try:
        outputs = pid.build(data, out_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"error: pid generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Generated {len(outputs)} sheet(s):")
    for o in outputs:
        print(f"  - {o}")
    return _maybe_plot(args, outputs, out_dir)


def _cmd_bfd(args: argparse.Namespace) -> int:
    from . import bfd

    data = Path(args.data)
    if not data.exists():
        print(f"error: data not found: {data}", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if args.out else (data.parent / "out")
    try:
        outputs = bfd.build(data, out_dir, view=args.view)
    except Exception as exc:  # noqa: BLE001
        print(f"error: bfd generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Generated {len(outputs)} sheet(s):")
    for o in outputs:
        print(f"  - {o}")
    return _maybe_plot(args, outputs, out_dir)


def _maybe_plot(args: argparse.Namespace, outputs, out_dir: Path) -> int:
    """``--pdf``: additionally plot what a generator wrote. Opt-in, defaulting off.

    ``build()`` return values are unchanged (a caller reads ``len(outputs)``), and
    no new file appears in ``out/`` unless asked for.
    """
    if not getattr(args, "pdf", False):
        return 0
    sheets = [
        Path(path) for path in outputs if Path(path).suffix.lower() in (".svg", ".dxf")
    ]
    if not sheets:
        print("error: --pdf: the generator produced no .svg or .dxf to plot", file=sys.stderr)
        return 2
    return _run_plot_requests(sheets, out_dir, _plot_defaults())


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
