"""CLI support for component specs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from ..dxf import DxfBuilder
from ..layers import LayerTableError, resolve_layer_table
from ..provenance import EmitPolicy, Manifest, ProvenanceError, write_json_canonical
from ..svg import ViewBox, svg_wrap
from .dimensions import (
    DimensionError,
    check_dimensions,
    dump_measurements,
    dump_setting_out,
    effective_layout,
    load_dimensions,
    measure,
    render_dimensions,
    setting_out_rows,
    setting_out_table,
)
from .emit import to_dxf, to_geojson, to_svg
from .layout import (
    Check,
    LayoutError,
    RegisterDrift,
    build_layout,
    canonical_instances,
    canonicalise_register,
    check_layout,
    check_register,
    derive_placements,
    dump_placements_register,
    layout_instance_properties,
    load_layout,
    placement_instance,
    snap_groups,
)
from .place import PlacedFeature, place
from .spec import ComponentSpecError, Point, load_component


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "component",
        help="place a to-scale component spec and emit GeoJSON, SVG, or DXF",
    )
    parser.add_argument("spec", help="component spec YAML")
    parser.add_argument("--view", default="plan", help="view name to emit (default: plan)")
    parser.add_argument("--emit", choices=["geojson", "svg", "dxf"], required=True)
    parser.add_argument("--out", required=True, help="output path")
    parser.add_argument(
        "--layers",
        help="layer table path, or 'default' for the packaged table (opt-in)",
    )
    parser.add_argument(
        "--plot-scale",
        type=float,
        help="1:N denominator for DXF custom linetype patterns when --layers is used",
    )
    placement = parser.add_mutually_exclusive_group()
    placement.add_argument("--origin", help="single-instance origin as 'E,N' (default: 0,0)")
    placement.add_argument("--place", help="placements YAML for multiple instances")
    parser.add_argument("--rot", type=float, default=0.0, help="single-instance rotation degrees")
    parser.add_argument("--pdf", action="store_true", help=_PDF_FLAG_HELP)
    parser.set_defaults(func=run)


#: ``--pdf`` is opt-in on every generator: "no new files appear in out/ unless
#: asked" is the cleanest reading of the backward-compatibility constraint, and it
#: costs one flag.
_PDF_FLAG_HELP = (
    "additionally plot the emitted sheet to PDF through technical_drawings_for_agents.plot "
    "(opt-in; the emitted geometry bytes are unchanged)"
)


def _plot_emitted(args: argparse.Namespace, out: Path) -> int:
    """Route an emitted sheet through THE plot entry point. Never a second backend."""
    if not getattr(args, "pdf", False):
        return 0
    if out.suffix.lower() not in (".svg", ".dxf"):
        print(
            f"error: --pdf needs an .svg or .dxf emit target, got {out.name}", file=sys.stderr
        )
        return 2
    from ..plot import PlotError, plot_source

    try:
        result = plot_source(out, out.parent)
    except PlotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    for path in result.outputs:
        print(
            f"PLOT {path.suffix.lstrip('.')} {path} "
            f"({result.svg_backend}, {result.pages}p, fidelity {result.state})"
        )
    return 0


def run(args: argparse.Namespace) -> int:
    try:
        component = load_component(args.spec)
        layer_table = resolve_layer_table(args.layers)
        placements = _load_placements(args.place) if args.place else [_single_placement(args)]

        all_placed: list[PlacedFeature] = []
        geojson_features: list[dict[str, Any]] = []
        for item in placements:
            features = place(
                component,
                args.view,
                origin=item["origin"],
                rotation_deg=item["rotation_deg"],
            )
            all_placed.extend(features)
            if args.emit == "geojson":
                geojson = to_geojson(features, instance=item["properties"])
                geojson_features.extend(geojson["features"])

        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if args.emit == "geojson":
            collection = {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:32630"}},
                "features": geojson_features,
            }
            out.write_text(json.dumps(collection, indent=2), encoding="utf-8", newline="\n")
        elif args.emit == "svg":
            vb = _viewbox_for(all_placed)
            elements = "\n".join(to_svg(all_placed, vb, pens=layer_table))
            group = f'<g class="components">\n{elements}\n</g>'
            out.write_text(
                svg_wrap(group, 1000, 700, background="#ffffff"),
                encoding="utf-8",
                newline="\n",
            )
        elif args.emit == "dxf":
            dxf = DxfBuilder(units="m", layer_table=layer_table, plot_scale=args.plot_scale)
            to_dxf(all_placed, dxf)
            dxf.save(out)
        else:
            raise ComponentSpecError(f"unknown emit target {args.emit!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"error: component generation failed: {exc}", file=sys.stderr)
        return 1

    print(f"component: {len(placements)} instance(s), {len(all_placed)} feature(s) -> {out}")
    return _plot_emitted(args, out)


def add_layout_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "layout",
        help="build + check a whole site layout from a placements register",
    )
    parser.add_argument("config", help="site layout config YAML")
    parser.add_argument("--view", default="plan", help="component view to place (default: plan)")
    parser.add_argument(
        "--from-geojson",
        help="refresh the placements register from a GeoJSON export of the editable layer",
    )
    # The three register flags are NOT an argparse mutually-exclusive group: that
    # is a flat "at most one of these" and cannot express "--from-geojson may pair
    # with --check-register, but --canonicalise-register excludes both". The
    # relationship is enforced in run_layout, and a test pins it there.
    parser.add_argument(
        "--check-register",
        action="store_true",
        help="compare the register with canonical output and exit 1 on drift; never writes. "
        "With --from-geojson, compares against a fresh export instead of writing. Register "
        "drift is the tool disagreeing with its own input, so --warn-only does NOT downgrade it",
    )
    parser.add_argument(
        "--canonicalise-register",
        action="store_true",
        help="rewrite the placements register in canonical order in place, from its own "
        "contents and with no export (the migration tool); cannot be combined with "
        "--from-geojson or --check-register",
    )
    parser.add_argument(
        "--register-source",
        help="provenance text to stamp into '# Derived from:' when canonicalising a register "
        "that has no such line",
    )
    parser.add_argument(
        "--require-ids",
        action="store_true",
        help="append an error-severity ids-present check, so CI can enforce identity "
        "coverage without editing the project YAML",
    )
    parser.add_argument("--emit", choices=["geojson", "dxf"], help="emit placed layout geometry")
    parser.add_argument("--out", help="output path for --emit")
    parser.add_argument(
        "--layers",
        help="layer table path, or 'default' for the packaged table (overrides config layers:)",
    )
    parser.add_argument(
        "--plot-scale",
        type=float,
        help="1:N denominator for DXF custom linetype patterns when a layer table is used",
    )
    parser.add_argument(
        "--check-only", action="store_true", help="run the layout checks and emit nothing"
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="report failing checks but exit 0 (default: any error finding exits 1)",
    )
    parser.add_argument(
        "--no-snap",
        action="store_true",
        help="skip the groups: snap rules and place exactly as recorded (as-placed review)",
    )
    parser.add_argument(
        "--emit-register",
        help="write the EFFECTIVE (post-snap) placements, so sheet/BOQ consumers read "
        "the same poses the emitted geometry was built from",
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="write <out>.manifest.json and use canonical (deterministic) emit for --emit "
        "output; without it the emitted bytes are exactly as before",
    )
    parser.add_argument("--pdf", action="store_true", help=_PDF_FLAG_HELP)
    parser.set_defaults(func=run_layout)


def run_layout(args: argparse.Namespace) -> int:
    try:
        _reject_conflicting_register_flags(args)
        if args.canonicalise_register:
            register, crs = _register_target(args.config)
            wrote = canonicalise_register(register, crs=crs, source=args.register_source)
            print(f"register: {'canonicalised' if wrote else 'already canonical'} -> {register}")
            return 0
        if args.check_register:
            return _check_register_command(args)
        if args.from_geojson:
            _refresh_register(args)
        layout = load_layout(args.config)
        layer_table = resolve_layer_table(args.layers) if args.layers else layout.layer_table
        if args.require_ids:
            layout = replace(
                layout,
                checks=[*layout.checks, Check(kind="ids-present", params={"severity": "error"})],
            )
        snap_report: list[str] = []
        if not args.no_snap:
            layout, snap_report = snap_groups(layout)
        placed = build_layout(layout, view=args.view)
        findings = check_layout(layout)
    except (LayoutError, ComponentSpecError, LayerTableError) as exc:
        print(f"error: layout failed: {exc}", file=sys.stderr)
        return 2

    print(
        f"layout {layout.id}: {len(layout.placements)} placement(s), "
        f"{len(placed)} feature(s), {len(layout.checks)} check(s)"
    )
    for line in snap_report:
        print(f"  snap: {line}")
    if args.no_snap and layout.groups:
        print(f"  snap: SKIPPED (--no-snap) — {len(layout.groups)} group rule(s) not applied")
    by_type: dict[str, int] = {}
    for placement in layout.placements:
        by_type[placement.type] = by_type.get(placement.type, 0) + 1
    for type_name in sorted(by_type):
        print(f"  - {type_name}: {by_type[type_name]}")

    if args.emit_register:
        effective = Path(args.emit_register)
        effective.parent.mkdir(parents=True, exist_ok=True)
        effective.write_text(
            dump_placements_register(
                canonical_instances([placement_instance(p) for p in layout.placements]),
                source=f"{layout.source.name} (effective: post-snap)",
                crs=layout.crs,
            ),
            encoding="utf-8",
            newline="\n",
        )
        print(f"effective register: {effective}")

    pdf_code = 0
    if not args.check_only and args.emit:
        if not args.out:
            print("error: --emit requires --out", file=sys.stderr)
            return 2
        policy = None
        if args.manifest:
            try:
                policy = EmitPolicy.from_environment()
            except ProvenanceError as exc:
                print(f"error: layout manifest failed: {exc}", file=sys.stderr)
                return 2
        try:
            out = _emit_layout(
                layout,
                placed,
                args.emit,
                Path(args.out),
                policy=policy,
                layer_table=layer_table,
                plot_scale=args.plot_scale,
            )
            if args.manifest:
                manifest = _write_layout_manifest(
                    out,
                    args.emit,
                    layout,
                    policy,
                    params={"snap": not args.no_snap, "view": args.view},
                )
                print(f"manifest: {manifest}")
        except (LayoutError, ComponentSpecError, LayerTableError, ProvenanceError, OSError) as exc:
            print(f"error: layout emit failed: {exc}", file=sys.stderr)
            return 2
        print(f"wrote: {out}")
        pdf_code = _plot_emitted(args, out)
    elif args.manifest:
        print("error: --manifest requires --emit and --out", file=sys.stderr)
        return 2

    errors = [f for f in findings if f.severity == "error"]
    if findings:
        sys.stdout.flush()  # keep the report in order when findings go to stderr
        stream = sys.stderr if errors else sys.stdout
        print(f"layout checks: {len(findings)} finding(s)", file=stream)
        for finding in findings:
            print(f"  {finding}", file=stream)
    else:
        print("layout checks: clean")

    # A layout check failure outranks a plot failure: the geometry is wrong before
    # the sheet is.
    if errors and not args.warn_only:
        return 1
    return pdf_code


def add_dimensions_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "dimensions",
        help="compute dimensions and a setting-out table from a placed layout",
        description="A dimensions file declares WHAT to measure. Every number is computed from "
        "the same placed geometry the sheet draws, with the same distance code the layout checks "
        "use. No field anywhere supplies the displayed value.",
    )
    parser.add_argument("config", help="<drawing>.dimensions.yaml")
    parser.add_argument("--layout", help="layout config, overriding dimension_set.layout")
    parser.add_argument(
        "--emit",
        choices=["svg", "csv", "yaml", "json"],
        help="svg = the dimension elements and setting-out table; csv/yaml = the setting-out "
        "table; json = the measurement register (the quantities interface)",
    )
    parser.add_argument("--out", help="output path for --emit")
    parser.add_argument(
        "--setting-out",
        dest="setting_out",
        help="also write the setting-out sidecar; the format follows the file suffix",
    )
    parser.add_argument(
        "--check-only", action="store_true", help="measure and check, emit nothing"
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="report failing checks but exit 0 (default: any error finding exits 1)",
    )
    parser.add_argument(
        "--no-snap",
        action="store_true",
        help="force snap: as-placed and stamp it in the output, so an as-placed measurement can "
        "never be mistaken for the design value",
    )
    parser.set_defaults(func=run_dimensions)


def run_dimensions(args: argparse.Namespace) -> int:
    """Mirror ``run_layout``'s contract: 2 = could not compute, 1 = computed and wrong, 0 = clean.

    Strictly read-only on the register, the layout config and ``meta.yaml``. Nothing here
    writes ``for_construction`` or a drawing's issue state; a fully dimensioned,
    setting-out-tabled sheet is still CONCEPT until the responsible engineer signs it.
    """

    try:
        dset = load_dimensions(args.config, layout_path=args.layout)
        if args.no_snap:
            dset = replace(dset, snap="as-placed")
        layout = load_layout(dset.layout_path)
        snapped, snap_report = effective_layout(dset, layout)
        measurements = measure(dset, layout)
        rows = setting_out_rows(dset, layout) if dset.setting_out is not None else []
        findings = check_dimensions(dset, measurements, rows=rows if rows else None)
    except (DimensionError, LayoutError, ComponentSpecError, LayerTableError, OSError) as exc:
        print(f"error: dimensions failed: {exc}", file=sys.stderr)
        return 2

    # The chain count is appended only when there are chains: a spec that predates P11
    # prints the line it always printed.
    chains = f", {len(dset.chains)} chain(s)" if dset.chains else ""
    print(
        f"dimensions {dset.id}: {len(measurements)} dimension(s), {len(dset.routes)} route(s), "
        f"{len(rows)} setting-out row(s){chains}"
    )
    stamp = " (as-placed: NOT the design pose)" if dset.snap == "as-placed" else ""
    print(f"  snap: {dset.snap}{stamp}")
    for line in snap_report:
        print(f"  snap: {line}")
    for measurement in measurements:
        print(f"  {_dimension_line(measurement)}")

    try:
        code = _emit_dimensions(args, dset, snapped, measurements, rows)
    except (DimensionError, LayoutError, ProvenanceError, OSError) as exc:
        print(f"error: dimensions emit failed: {exc}", file=sys.stderr)
        return 2
    if code:
        return code

    errors = [f for f in findings if f.severity == "error"]
    if findings:
        sys.stdout.flush()
        stream = sys.stderr if errors else sys.stdout
        print(f"dimension checks: {len(findings)} finding(s)", file=stream)
        for finding in findings:
            print(f"  {finding}", file=stream)
    else:
        print("dimension checks: clean")
    if errors and not args.warn_only:
        return 1
    return 0


def _dimension_line(measurement) -> str:
    spec = measurement.spec
    refs = measurement.provenance.get("refs", {})
    if spec.kind == "envelope":
        subject = ",".join(f"{k}:{v}" for k, v in (spec.of or {}).items())
    elif spec.kind == "chainage":
        subject = str(spec.route)
    else:
        subject = f"{refs.get('from', '?')} -> {refs.get('to', '?')}"
    if spec.axis:
        subject = f"{subject} ({spec.axis})"
    expectation = ""
    if spec.expect_m is not None:
        ok = abs(measurement.value_m - spec.expect_m) <= spec.style.expect_tol_m
        expectation = (
            f"   (expect {spec.expect_m:g} ±{spec.style.expect_tol_m:g} — "
            f"{'ok' if ok else 'MISMATCH'})"
        )
    return f"{spec.id:<9} {spec.kind:<10} {subject:<44} {measurement.text}{expectation}"


def _emit_dimensions(args, dset, layout, measurements, rows) -> int:
    if args.setting_out:
        if not rows:
            print(
                "error: --setting-out needs a setting_out: block in the dimensions file",
                file=sys.stderr,
            )
            return 2
        target = Path(args.setting_out)
        fmt = "yaml" if target.suffix.lower() in (".yaml", ".yml") else "csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            dump_setting_out(
                rows, dset.setting_out, crs=layout.crs, fmt=fmt, snap=dset.snap
            ),
            encoding="utf-8",
            newline="\n",
        )
        print(f"setting out: {len(rows)} row(s) -> {target}")
    if args.check_only or not args.emit:
        if args.emit and args.check_only:
            print("  emit: SKIPPED (--check-only)")
        return 0
    if not args.out:
        print("error: --emit requires --out", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.emit == "json":
        text = dump_measurements(dset, measurements)
    elif args.emit in ("csv", "yaml"):
        if not rows:
            print(
                f"error: --emit {args.emit} needs a setting_out: block in the dimensions file",
                file=sys.stderr,
            )
            return 2
        text = dump_setting_out(
            rows, dset.setting_out, crs=layout.crs, fmt=args.emit, snap=dset.snap
        )
    else:
        text = _dimensions_svg(dset, layout, measurements, rows)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote: {out}")
    return 0


def _dimensions_svg(dset, layout, measurements, rows) -> str:
    """A review sheet of just the dimensions. Never touches a drawing's meta.yaml."""

    points: list[Point] = [
        point
        for measurement in measurements
        for point in (measurement.anchor_a, measurement.anchor_b, *measurement.path)
    ]
    for _, feature in build_layout(layout):
        if isinstance(feature.coords, tuple):
            points.append(feature.coords)
        else:
            points.extend(feature.coords)
    vb = _viewbox_for_points(points)
    table = (
        setting_out_table(rows, dset.setting_out, crs=layout.crs, snap=dset.snap)
        if rows
        else None
    )
    elements, boxes = render_dimensions(vb, measurements, setting_out=table)
    comment = (
        f"<!-- dimension set {dset.id}; layout {layout.id}; poses: {dset.snap}; "
        f"{len(boxes)} annotation box(es) for the legibility checks -->"
    )
    body = "\n".join([comment, '<g class="dimensions">', *elements, "</g>"])
    return svg_wrap(body, 1000, 700, background="#ffffff")


def _viewbox_for_points(points: list[Point]) -> ViewBox:
    if not points:
        return ViewBox(-1, 1, -1, 1, 1000, 700, padding=60)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    if xmin == xmax:
        xmin, xmax = xmin - 1.0, xmax + 1.0
    if ymin == ymax:
        ymin, ymax = ymin - 1.0, ymax + 1.0
    pad_x = max((xmax - xmin) * 0.12, 0.5)
    pad_y = max((ymax - ymin) * 0.12, 0.5)
    return ViewBox(xmin - pad_x, xmax + pad_x, ymin - pad_y, ymax + pad_y, 1000, 700, padding=70)


def _refresh_register(args: argparse.Namespace) -> None:
    """Derive the placements register from a GIS export, before loading the layout.

    The config is read twice on purpose: once shallowly for the editor block and
    register path (the register may not exist yet, so a full load would fail),
    then fully once the register is on disk.
    """

    config_path, raw = _read_layout_config(args.config)
    register, crs = _register_target(config_path)
    instances, export = _derive_from_export(args.from_geojson, raw)
    canonical = canonical_instances(instances)
    # Reject a type with no components.types entry BEFORE the first byte is
    # written. The old flow wrote the register and then failed on load, leaving a
    # generated artifact on disk that had never validated — so a later run could
    # load it. Failing early is strictly better.
    known = _known_component_types(raw, config_path.name)
    _validate_known_types(canonical, known, config_path.name)
    text = dump_placements_register(canonical, source=export.name, crs=crs)
    if register.is_file() and register.read_text(encoding="utf-8") == text:
        # Reported, not silent — and the mtime stays put, which P2's (#54)
        # staleness DAG will key on.
        print(f"register: unchanged ({len(canonical)} placement(s)) -> {register}")
        return
    register.parent.mkdir(parents=True, exist_ok=True)
    register.write_text(text, encoding="utf-8", newline="\n")
    print(f"register: {len(canonical)} placement(s) -> {register}")


def _reject_conflicting_register_flags(args: argparse.Namespace) -> None:
    """Enforce the flag relationship argparse cannot express (see add_layout_parser)."""

    if args.canonicalise_register and (args.from_geojson or args.check_register):
        conflicting = " and ".join(
            flag
            for flag, present in (
                ("--from-geojson", bool(args.from_geojson)),
                ("--check-register", bool(args.check_register)),
            )
            if present
        )
        raise LayoutError(
            f"--canonicalise-register cannot be combined with {conflicting}: deriving or "
            "checking and canonicalising in place are different intents, and combining them "
            "is how a stale export overwrites a good register"
        )
    if args.register_source and not args.canonicalise_register:
        raise LayoutError("--register-source is only used with --canonicalise-register")


def _check_register_command(args: argparse.Namespace) -> int:
    if args.emit or args.emit_register or args.out or args.manifest:
        raise LayoutError(
            "--check-register never writes; drop --emit, --emit-register, --out and --manifest"
        )
    layout = load_layout(args.config)
    instances = None
    if args.from_geojson:
        _, raw = _read_layout_config(args.config)
        instances, _ = _derive_from_export(args.from_geojson, raw)
    drift = check_register(layout, instances=instances)
    return _report_register_check(drift, len(instances) if instances else len(layout.placements))


def _report_register_check(drift: RegisterDrift, count: int) -> int:
    if drift.clean:
        print(f"register: clean ({count} placement(s)) -> {drift.register}")
        return 0
    sys.stdout.writelines(drift.unified_diff())
    print(f"error: register drift: {drift.register}", file=sys.stderr)
    return 1


def _derive_from_export(
    export_arg: str, raw: dict[str, Any]
) -> tuple[list[dict[str, Any]], Path]:
    editor = raw.get("editor") or {}
    if not isinstance(editor, dict):
        raise LayoutError("editor must be a mapping")
    export = Path(export_arg)
    if not export.is_file():
        raise LayoutError(f"GeoJSON export not found: {export}")
    geojson = json.loads(export.read_text(encoding="utf-8"))
    instances = derive_placements(
        geojson,
        type_field=_editor_field(editor, "type_field", "type"),
        tag_field=_editor_field(editor, "tag_field", "tag"),
        id_field=_editor_field(editor, "id_field", "id"),
        exclude=editor.get("parked") or [],
    )
    if not instances:
        raise LayoutError(
            f"{export.name}: every feature fell in a parked zone — nothing to place"
        )
    return instances, export


def _read_layout_config(config: str | Path) -> tuple[Path, dict[str, Any]]:
    """Read the config shallowly — the register may not exist yet, so a full load would fail."""

    config_path = Path(config).resolve()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise LayoutError(f"cannot read {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise LayoutError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LayoutError(f"{config_path.name}: top level must be a mapping")
    return config_path, raw


def _register_target(config: str | Path) -> tuple[Path, str]:
    config_path, raw = _read_layout_config(config)
    register_ref = raw.get("placements")
    if not isinstance(register_ref, str):
        raise LayoutError(
            "--from-geojson / --check-register / --canonicalise-register need placements: "
            "to be a register path, not an inline list"
        )
    layout_block = raw.get("layout") or {}
    if not isinstance(layout_block, dict):
        raise LayoutError(f"{config_path.name}: layout must be a mapping")
    return (config_path.parent / register_ref).resolve(), str(layout_block.get("crs", "EPSG:32630"))


def _editor_field(editor: dict[str, Any], key: str, default: str) -> str:
    value = editor.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise LayoutError(f"editor.{key} must be a non-empty string")
    return value.strip()


def _known_component_types(raw: dict[str, Any], ctx: str) -> set[str]:
    components = raw.get("components")
    if not isinstance(components, dict):
        raise LayoutError(f"{ctx}: missing components: {{root, types}}")
    types = components.get("types")
    if not isinstance(types, dict) or not types:
        raise LayoutError(f"{ctx}: components.types must be a non-empty mapping")
    return {str(name) for name in types}


def _validate_known_types(
    instances: list[dict[str, Any]], known_types: set[str], ctx: str
) -> None:
    unknown = sorted({str(instance["type"]) for instance in instances} - known_types)
    if unknown:
        raise LayoutError(
            f"{ctx}: placement type(s) with no components.types entry: {', '.join(unknown)}"
        )


def _emit_layout(
    layout,
    placed,
    emit: str,
    out: Path,
    policy=None,
    layer_table=None,
    plot_scale=None,
) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if emit == "geojson":
        features: list[dict[str, Any]] = []
        for placement, feature in placed:
            collection = to_geojson(
                [feature],
                crs=layout.crs,
                instance=layout_instance_properties(placement),
                policy=policy,
            )
            features.extend(collection["features"])
        collection = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": layout.crs}},
            "features": features,
        }
        if policy is not None:
            write_json_canonical(out, collection, policy)
        else:
            # Unrounded coordinates and source key order: this file is a live
            # input to a live sheet, so its bytes only move when a caller opts in.
            out.write_text(json.dumps(collection, indent=2), encoding="utf-8", newline="\n")
        return out
    dxf = DxfBuilder(units="m", policy=policy, layer_table=layer_table, plot_scale=plot_scale)
    to_dxf([feature for _, feature in placed], dxf)
    dxf.save(out)
    return out


def _layout_manifest_inputs(layout) -> list[Path]:
    """Every file the emitted geometry was derived from: config, register, specs."""

    inputs = [layout.source]
    if layout.register is not None:
        inputs.append(layout.register)
    for specs in layout.types.values():
        inputs.extend(specs)
    # A spec may be shared by several types; the manifest declares each file once.
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in inputs:
        resolved = Path(path).resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _write_layout_manifest(
    out: Path,
    target_format: str,
    layout,
    policy,
    params: dict[str, Any],
) -> Path:
    inputs = _layout_manifest_inputs(layout)
    manifest = Manifest.plan(
        target=out,
        target_format=target_format,
        inputs=inputs,
        root=_common_root(out, inputs),
        params=params,
        policy=policy,
        meta=None,
    ).with_output(out)
    return manifest.write()


def _common_root(target: Path, inputs: list[Path]) -> Path:
    """The narrowest directory containing the output and every declared input."""

    paths = [target.parent.resolve(), *(path.resolve() for path in inputs)]
    return Path(os.path.commonpath([str(path) for path in paths]))


def _single_placement(args: argparse.Namespace) -> dict[str, Any]:
    origin = _parse_pair(args.origin or "0,0", "--origin")
    return {"origin": origin, "rotation_deg": args.rot, "properties": {}}


def _load_placements(path: str | Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("instances"), list):
        raise ComponentSpecError("placements YAML must contain instances: [...]")

    placements: list[dict[str, Any]] = []
    for idx, raw in enumerate(data["instances"]):
        if not isinstance(raw, dict):
            raise ComponentSpecError(f"instances[{idx}] must be a mapping")
        if "origin_utm" not in raw:
            raise ComponentSpecError(f"instances[{idx}] missing origin_utm")
        origin = _coord(raw["origin_utm"], f"instances[{idx}].origin_utm")
        rotation = raw.get("rotation_deg", 0.0)
        if isinstance(rotation, bool) or not isinstance(rotation, (int, float)):
            raise ComponentSpecError(f"instances[{idx}].rotation_deg must be a number")
        properties = {
            key: value for key, value in raw.items() if key not in {"origin_utm", "rotation_deg"}
        }
        placements.append(
            {"origin": origin, "rotation_deg": float(rotation), "properties": properties}
        )
    if not placements:
        raise ComponentSpecError("placements YAML must contain at least one instance")
    return placements


def _parse_pair(value: str, ctx: str) -> Point:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        raise ComponentSpecError(f"{ctx} must be formatted as 'x,y'")
    try:
        return (float(parts[0]), float(parts[1]))
    except ValueError as exc:
        raise ComponentSpecError(f"{ctx} must contain numeric x,y values") from exc


def _coord(value: Any, ctx: str) -> Point:
    if not isinstance(value, list) or len(value) != 2:
        raise ComponentSpecError(f"{ctx} must be [x, y]")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ComponentSpecError(f"{ctx} must contain numeric x,y values")
    return (float(value[0]), float(value[1]))


def _viewbox_for(features: list[PlacedFeature]) -> ViewBox:
    points: list[Point] = []
    for feature in features:
        if feature.kind == "circle":
            x, y = _single_point(feature.coords)
            radius = feature.radius or 0.0
            points.extend([(x - radius, y - radius), (x + radius, y + radius)])
        elif isinstance(feature.coords, tuple):
            points.append(feature.coords)
        else:
            points.extend(feature.coords)
    if not points:
        return ViewBox(-1, 1, -1, 1, 1000, 700, padding=60)

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmin == xmax:
        xmin -= 1.0
        xmax += 1.0
    if ymin == ymax:
        ymin -= 1.0
        ymax += 1.0
    pad_x = max((xmax - xmin) * 0.08, 0.5)
    pad_y = max((ymax - ymin) * 0.08, 0.5)
    return ViewBox(
        xmin - pad_x,
        xmax + pad_x,
        ymin - pad_y,
        ymax + pad_y,
        1000,
        700,
        padding=50,
    )


def _single_point(coords: Point | list[Point]) -> Point:
    if isinstance(coords, tuple):
        return coords
    raise TypeError("expected point coordinates")
