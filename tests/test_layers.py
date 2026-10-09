"""The layer / lineweight / linetype table, and the three artifacts it drives.

Two comparisons in here are made through :func:`technical_drawings_for_agents.provenance.mask_dxf_volatiles`
rather than on raw bytes, and deliberately so (P7-FINAL Correction 1, repo issue #101).
``ezdxf`` re-rolls two GUIDs and rewrites ``$TDCREATE`` / ``$TDUPDATE`` and its two
marker strings on *every* save, so two separately-created documents can never be
byte-equal, and the DXF artifacts committed under ``drawings/example/*/out/`` predate
P3 and carry that older metadata. P3 shipped the masking primitive and the masked
golden precisely so a DXF can be compared for *drawing* equality; nothing here
touches ``ezdxf.options.write_fixed_meta_data_for_testing`` — that process-global is
P3's, reached only through an :class:`~technical_drawings_for_agents.provenance.EmitPolicy`.
"""

from __future__ import annotations

import logging
import runpy
import shutil
import sys
from io import StringIO
from pathlib import Path

import pytest

from technical_drawings_for_agents.layers import (
    LayerSpec,
    LayerTable,
    LayerTableError,
    LinetypeSpec,
    default_layer_table,
    load_layer_table,
)
from technical_drawings_for_agents.provenance import mask_dxf_volatiles

GOLDENS = Path(__file__).resolve().parent / "goldens"

EXAMPLE_COMPONENT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "technical_drawings_for_agents"
    / "components"
    / "examples"
    / "packaged_unit.yaml"
)
SIMPLE_SECTION = Path(__file__).resolve().parents[1] / "drawings" / "example" / "simple-section"

STOCK_LINETYPES = {
    "BYBLOCK",
    "BYLAYER",
    "CONTINUOUS",
    "CENTER",
    "CENTER2",
    "CENTERX2",
    "DASHDOT",
    "DASHDOT2",
    "DASHDOTX2",
    "DASHED",
    "DASHED2",
    "DASHEDX2",
    "DIVIDE",
    "DIVIDE2",
    "DIVIDEX2",
    "DOT",
    "DOT2",
    "DOTX2",
    "PHANTOM",
    "PHANTOM2",
    "PHANTOMX2",
}


def _table(**overrides) -> LayerTable:
    values = {
        "layers": (
            LayerSpec(
                name="FDN",
                aci=3,
                lineweight_mm=0.35,
                linetype="CONTINUOUS",
                description="Foundations, slabs, plinths",
            ),
            LayerSpec(name="ZONE", aci=9, lineweight_mm=0.18, linetype="SK_HIDDEN"),
            LayerSpec(name="TEXT", aci=7, lineweight_mm=0.18),
        ),
        "linetypes": (
            LinetypeSpec(
                name="SK_HIDDEN",
                pattern_mm=(3.0, -2.0),
                description="Hidden detail — 3 mm dash, 2 mm gap on paper",
            ),
        ),
        "source": Path("test-layers.yaml"),
    }
    values.update(overrides)
    return LayerTable(**values)


def _stock_table(*layers: LayerSpec, **overrides) -> LayerTable:
    values = {
        "layers": layers
        or (
            LayerSpec(
                name="FDN",
                aci=3,
                lineweight_mm=0.35,
                linetype="CONTINUOUS",
                description="Foundations, slabs, plinths",
            ),
        ),
        "linetypes": (),
        "source": Path("stock-layers.yaml"),
    }
    values.update(overrides)
    return LayerTable(**values)


def test_default_layer_table_loads_and_declares_every_layer_in_use():
    table = default_layer_table()

    assert isinstance(table, LayerTable)
    assert set(table.names()).issuperset(
        {
            "OUTLINE",
            "OBJECT",
            "DIMENSIONS",
            "CENTRE",
            "HATCH",
            "TEXT",
            "TITLEBLOCK",
            "WATERMARK",
            "FDN",
            "EQUIP",
            "NOZZLE",
            "ZONE",
            "WATER",
            "ACCESS",
        }
    )
    custom = {linetype.name.casefold() for linetype in table.linetypes}
    for layer in table.layers:
        assert layer.linetype.upper() in STOCK_LINETYPES or layer.linetype.casefold() in custom
    assert table.version == 1


def test_default_table_preserves_the_existing_aci_colours():
    from technical_drawings_for_agents.dxf import LAYERS

    table = default_layer_table()

    for name, attribs in LAYERS.items():
        assert table.get(name).aci == attribs["color"]


def test_the_equipment_versus_equip_collision_is_still_open(tmp_path):
    """P7 open question 3, pinned as a test instead of left in prose.

    The packaged component example declares ``EQUIPMENT``; the live plant component
    specs use ``EQUIP`` (×12). There is deliberately no alias mechanism, so one of
    the two names has to change, and which one is a project-data decision for the owner
    — not something an implementer may pick. Until it is answered, the packaged
    example opting into the default table **must fail loudly**; that is the signal
    working, not a defect.

    When this test fails, the question has been answered: record the answer in the
    spec's section 8 and update this test to match.
    """

    from technical_drawings_for_agents.dxf import DxfBuilder

    table = default_layer_table()
    assert "EQUIPMENT" in EXAMPLE_COMPONENT.read_text(encoding="utf-8")
    assert table.has("EQUIP")
    assert not table.has("EQUIPMENT")

    with pytest.raises(LayerTableError) as exc:
        DxfBuilder(layer_table=table, plot_scale=200).line((0, 0), (1, 0), layer="EQUIPMENT")

    assert "EQUIPMENT" in str(exc.value)
    assert "EQUIP" in str(exc.value)


def test_applied_dxf_has_expected_layer_records(tmp_path):
    import ezdxf
    from technical_drawings_for_agents.dxf import DxfBuilder

    dxf = DxfBuilder(layer_table=_table(), plot_scale=200)
    dxf.line((0, 0), (1, 0), layer="FDN")
    dxf.polyline([(0, 0), (1, 0), (1, 1)], layer="ZONE")
    dxf.text((0, 0), "T", layer="TEXT")
    out = dxf.save(tmp_path / "a.dxf")

    doc = ezdxf.readfile(out)
    assert doc.layers.has_entry("FDN")
    assert doc.layers.has_entry("ZONE")
    assert doc.layers.has_entry("TEXT")
    fdn = doc.layers.get("FDN")
    assert fdn.dxf.color == 3
    assert fdn.dxf.lineweight == 35
    assert fdn.dxf.linetype == "CONTINUOUS"
    assert fdn.dxf.plot == 1
    assert fdn.description == "Foundations, slabs, plinths"
    zone = doc.layers.get("ZONE")
    assert zone.dxf.lineweight == 18
    assert zone.dxf.linetype == "SK_HIDDEN"


def test_applied_dxf_enables_lineweight_display(tmp_path):
    import ezdxf
    from technical_drawings_for_agents.dxf import DxfBuilder

    with_table = DxfBuilder(layer_table=_stock_table())
    with_table.line((0, 0), (1, 0), layer="FDN")
    with_table_out = with_table.save(tmp_path / "with-table.dxf")

    legacy = DxfBuilder()
    legacy.line((0, 0), (1, 0))
    legacy_out = legacy.save(tmp_path / "legacy.dxf")

    assert ezdxf.readfile(with_table_out).header["$LWDISPLAY"] == 1
    assert ezdxf.readfile(legacy_out).header["$LWDISPLAY"] == 0


def test_true_colour_is_written_alongside_aci(tmp_path):
    import ezdxf
    from technical_drawings_for_agents.dxf import DxfBuilder

    table = _stock_table(
        LayerSpec(name="FDN", aci=3, lineweight_mm=0.35, true_color="#336699")
    )
    dxf = DxfBuilder(layer_table=table)
    dxf.line((0, 0), (1, 0), layer="FDN")
    doc = ezdxf.readfile(dxf.save(tmp_path / "true-color.dxf"))

    layer = doc.layers.get("FDN")
    assert layer.dxf.color == 3
    assert layer.dxf.true_color == 0x336699


@pytest.mark.parametrize(
    ("lineweight_mm", "expected"),
    [(0.25, 25), (0.35, 35), (0.36, 35), (0.45, 40), (0.515, 50), (0.0, 0), (2.11, 211)],
)
def test_lineweight_snaps_to_nearest_dxf_value(lineweight_mm, expected):
    assert LayerSpec(name="X", aci=1, lineweight_mm=lineweight_mm).dxf_lineweight == expected


@pytest.mark.parametrize("lineweight_mm", [2.5, -0.1])
def test_lineweight_outside_dxf_range_is_an_error(lineweight_mm):
    with pytest.raises(LayerTableError) as exc:
        LayerSpec(name="X", aci=1, lineweight_mm=lineweight_mm).dxf_lineweight

    message = str(exc.value)
    assert "X" in message
    assert str(lineweight_mm) in message
    assert "2.11" in message


def test_snapped_lineweight_is_reported(caplog):
    import ezdxf

    table = _stock_table(LayerSpec(name="FDN", aci=3, lineweight_mm=0.36))
    doc = ezdxf.new("R2018", setup=True)

    with caplog.at_level(logging.WARNING, logger="technical_drawings_for_agents.layers"):
        report = table.apply(doc)

    assert report.lineweights_snapped == (("FDN", 0.36, 0.35),)
    assert "FDN" in caplog.text
    assert "0.36" in caplog.text


def test_custom_linetype_pattern_is_written_in_drawing_units_at_plot_scale(tmp_path):
    import ezdxf
    from technical_drawings_for_agents.dxf import DxfBuilder

    dxf = DxfBuilder(layer_table=_table(), plot_scale=200)
    dxf.polyline([(0, 0), (1, 0)], layer="ZONE")
    out = dxf.save(tmp_path / "scale-200.dxf")
    doc = ezdxf.readfile(out)

    assert doc.linetypes.get("SK_HIDDEN").dxf.description == (
        "Hidden detail — 3 mm dash, 2 mm gap on paper"
    )
    tags = _linetype_tags(out, "SK_HIDDEN")
    assert tags[40] == pytest.approx([1.0])
    assert tags[49] == pytest.approx([0.6, -0.4])

    dxf_50 = DxfBuilder(layer_table=_table(), plot_scale=50)
    dxf_50.polyline([(0, 0), (1, 0)], layer="ZONE")
    tags_50 = _linetype_tags(dxf_50.save(tmp_path / "scale-50.dxf"), "SK_HIDDEN")
    assert tags_50[49] == pytest.approx([0.15, -0.10])


def test_custom_linetype_without_plot_scale_is_an_error():
    from technical_drawings_for_agents.dxf import DxfBuilder

    with pytest.raises(LayerTableError) as exc:
        DxfBuilder(layer_table=_table(), plot_scale=None)

    message = str(exc.value)
    assert "SK_HIDDEN" in message
    assert "paper mm" in message
    assert "plot_scale" in message

    DxfBuilder(layer_table=_stock_table(), plot_scale=None)


def test_pdf_pens_match_the_layer_table(tmp_path):
    import ezdxf
    from ezdxf.addons.drawing import RenderContext
    from technical_drawings_for_agents.dxf import DxfBuilder

    dxf = DxfBuilder(layer_table=_table(), plot_scale=200)
    dxf.line((0, 0), (1, 0), layer="FDN")
    dxf.polyline([(0, 0), (1, 0)], layer="ZONE")
    doc = ezdxf.readfile(dxf.save(tmp_path / "pens.dxf"))
    ctx = RenderContext(doc)

    by_layer = {entity.dxf.layer: ctx.resolve_all(entity) for entity in doc.modelspace()}
    assert by_layer["FDN"].lineweight == pytest.approx(0.35)
    assert by_layer["FDN"].color == "#00ff00"
    assert by_layer["ZONE"].lineweight == pytest.approx(0.18)
    assert by_layer["ZONE"].linetype_pattern == pytest.approx((0.6, 0.4))


def test_svg_pens_match_the_layer_table():
    from technical_drawings_for_agents.components.emit import to_svg
    from technical_drawings_for_agents.components.place import PlacedFeature
    from technical_drawings_for_agents.svg import ViewBox

    placed = [
        PlacedFeature(
            component="C",
            role="foundation",
            layer="FDN",
            tag=None,
            source_status="sourced",
            hatch=None,
            kind="line",
            coords=[(0.0, 0.0), (1.0, 0.0)],
        ),
        PlacedFeature(
            component="C",
            role="zone",
            layer="ZONE",
            tag=None,
            source_status="sourced",
            hatch=None,
            kind="line",
            coords=[(0.0, 1.0), (1.0, 1.0)],
        ),
    ]
    vb = ViewBox(0, 1, 0, 1, 100, 100, padding=0)

    elements = to_svg(placed, vb, pens=_table())

    assert any('stroke="#00ff00"' in element and 'stroke-width="1.32"' in element
               for element in elements)
    assert any('stroke-dasharray="11.34,7.56"' in element for element in elements)

    wide = to_svg(placed[:1], vb, pens=_table(), px_per_mm=10.0)
    assert 'stroke-width="3.5"' in wide[0]


def test_the_svg_pen_width_is_the_lineweight_the_dxf_actually_carries(tmp_path):
    """An authored width DXF cannot express must not survive in the SVG pen.

    The spec and both variants were silent on which value ``Pen.width_mm`` carries
    when the authored millimetres are not a DXF lineweight. It is the **written**
    one: 0.36 mm is written to the DXF as 0.35 mm, so a pen of 0.36 mm would put
    the SVG and the DXF-derived PDF a step apart on the same layer, which is the
    disagreement this table exists to remove.
    """

    import ezdxf
    from technical_drawings_for_agents.dxf import DxfBuilder

    table = _stock_table(LayerSpec(name="FDN", aci=3, lineweight_mm=0.36))
    pen = table.pen("FDN")

    assert pen.width_mm == pytest.approx(0.35)
    assert pen.width_px(96.0 / 25.4) == pytest.approx(1.3228, abs=1e-4)

    dxf = DxfBuilder(layer_table=table)
    dxf.line((0, 0), (1, 0), layer="FDN")
    doc = ezdxf.readfile(dxf.save(tmp_path / "snapped.dxf"))
    assert doc.layers.get("FDN").dxf.lineweight == 35
    assert doc.layers.get("FDN").dxf.lineweight / 100.0 == pytest.approx(pen.width_mm)


def test_svg_pen_for_aci7_follows_the_background():
    layer = LayerSpec(name="TEXT", aci=7, lineweight_mm=0.18)

    assert layer.ink("light") == "#111111"
    assert layer.ink("dark") == "#d9e2ec"


def test_pens_and_explicit_stroke_are_mutually_exclusive():
    from technical_drawings_for_agents.components.emit import to_svg
    from technical_drawings_for_agents.components.place import PlacedFeature
    from technical_drawings_for_agents.svg import ViewBox

    placed = [
        PlacedFeature(
            component="C",
            role="foundation",
            layer="FDN",
            tag=None,
            source_status="sourced",
            hatch=None,
            kind="line",
            coords=[(0.0, 0.0), (1.0, 0.0)],
        )
    ]
    vb = ViewBox(0, 1, 0, 1, 100, 100, padding=0)

    with pytest.raises(LayerTableError, match="pens=.*stroke"):
        to_svg(placed, vb, pens=_table(), stroke="#ff0000")
    with pytest.raises(LayerTableError, match="pens=.*stroke_width"):
        to_svg(placed, vb, pens=_table(), stroke_width=2.0)


def test_writing_to_an_undeclared_layer_raises_at_the_call_site(caplog):
    from technical_drawings_for_agents.dxf import DxfBuilder

    dxf = DxfBuilder(layer_table=_table(), plot_scale=200)

    with pytest.raises(LayerTableError) as exc:
        dxf.line((0, 0), (1, 1), layer="FND")

    assert "FND" in str(exc.value)
    assert "FDN" in str(exc.value)
    assert len(dxf.msp) == 0

    warn_table = _table(undeclared="warn")
    warn_dxf = DxfBuilder(layer_table=warn_table, plot_scale=200)
    with caplog.at_level(logging.WARNING, logger="technical_drawings_for_agents.layers"):
        warn_dxf.line((0, 0), (1, 1), layer="FND")

    assert len(warn_dxf.msp) == 1
    assert "FND" in caplog.text
    assert warn_dxf.doc.layers.get("FND").dxf.color == 7
    assert warn_dxf.layer_report.undeclared == ("FND",)


def test_dxf_export_without_a_layer_table_is_byte_identical_to_the_legacy_writer():
    """Required backward-compat test: no table means the pre-change writer, exactly.

    ``_legacy_build`` replicates the pre-P7 constructor line for line. The two
    documents are compared through ``mask_dxf_volatiles`` because they are two
    *separate* ``ezdxf.new()`` calls, so their GUIDs and creation timestamps are
    necessarily different — container metadata, not drawing content, and the only
    six items P3 measured as varying. Every other byte, including each layer
    record and its ordering, is compared verbatim.
    """

    from technical_drawings_for_agents.dxf import DxfBuilder

    legacy = _legacy_build()
    current = DxfBuilder()
    current.line((0, 0), (1, 0), layer="OUTLINE")
    current.polyline([(0, 0), (1, 0), (1, 1)], closed=True, layer="OBJECT")
    current.circle((2, 2), 0.5, layer="CENTRE")
    current.text((0, 1), "hello", layer="TEXT")

    legacy_stream = StringIO()
    current_stream = StringIO()
    legacy.write(legacy_stream)
    current.doc.write(current_stream)

    assert mask_dxf_volatiles(current_stream.getvalue()) == mask_dxf_volatiles(
        legacy_stream.getvalue()
    )
    # ...and the masking is not hiding a difference: every line that differs
    # unmasked is one of the documented volatile items.
    assert _volatile_only_difference(current_stream.getvalue(), legacy_stream.getvalue())
    assert current.doc.header["$LWDISPLAY"] == 0
    for layer in current.doc.layers:
        if layer.dxf.name in {"OUTLINE", "OBJECT", "DIMENSIONS", "CENTRE", "HATCH", "TEXT",
                              "TITLEBLOCK", "WATERMARK"}:
            assert layer.dxf.lineweight == -3
            assert layer.dxf.linetype == "Continuous"


def test_the_example_dxf_regenerates_to_the_masked_golden(tmp_path):
    """Required backward-compat test: the shipped generator's DXF has not moved.

    P7-FINAL Correction 1 / issue **#101**. The comparison target is P3's committed
    ``tests/goldens/EXA-CIV-SEC-001.dxf.masked``, **not** the artifact under
    ``drawings/example/simple-section/out/`` — that one predates P3 and differs from
    any fresh render in six lines of 13,532 ($TDCREATE, $TDUPDATE, both GUIDs and two
    ezdxf marker strings), which makes byte-equality against it unsatisfiable by
    construction. No literal ``sha256`` is pinned either (P3-FINAL Correction 4): a
    golden file fails with a readable diff, a digest cannot say what moved.
    """

    work = tmp_path / "simple-section"
    shutil.copytree(SIMPLE_SECTION, work)
    _run_source(work / "source.py")

    masked = mask_dxf_volatiles(
        (work / "out" / "EXA-CIV-SEC-001.dxf").read_text(encoding="utf-8", errors="ignore")
    )
    assert masked == (GOLDENS / "EXA-CIV-SEC-001.dxf.masked").read_text(encoding="utf-8")
    # The SVG side of the same generator is reproducible without masking at all.
    assert (work / "out" / "EXA-CIV-SEC-001.svg").read_bytes() == (
        GOLDENS / "EXA-CIV-SEC-001.svg"
    ).read_bytes()


def test_component_svg_without_pens_is_byte_identical():
    from technical_drawings_for_agents.components import load_component, place
    from technical_drawings_for_agents.components.emit import to_svg
    from technical_drawings_for_agents.svg import ViewBox

    placed = place(load_component(EXAMPLE_COMPONENT), "plan", origin=(0.0, 0.0))
    vb = ViewBox(-4, 4, -3, 3, 800, 600, padding=40)

    actual = "\n".join(to_svg(placed, vb))
    golden = (Path(__file__).resolve().parent / "goldens" / "packaged_unit_plan.svg").read_text(
        encoding="utf-8"
    )
    assert actual + "\n" == golden


@pytest.mark.parametrize(
    ("body", "substrings"),
    [
        (
            """
layer_table: {version: 1}
layers:
  - {name: FDN, aci: 3}
  - {name: fdn, aci: 4}
""",
            ("duplicate",),
        ),
        ("layer_table: {version: 1}\nlayers:\n  - {name: '0', aci: 3}\n", ("reserved",)),
        (
            "layer_table: {version: 1}\nlayers:\n  - {name: Defpoints, aci: 3}\n",
            ("reserved",),
        ),
        ("layer_table: {version: 1}\nlayers:\n  - {name: A/B, aci: 3}\n", ("invalid character",)),
        ("layer_table: {version: 1}\nlayers:\n  - {name: FDN, aci: 0}\n", ("aci",)),
        ("layer_table: {version: 1}\nlayers:\n  - {name: FDN, aci: 256}\n", ("aci",)),
        ("layer_table: {version: 1}\nlayers:\n  - {name: FDN, aci: 300}\n", ("aci",)),
        (
            "layer_table: {version: 1}\nlayers:\n  - {name: FDN, aci: 3, linetype: NOPE}\n",
            ("unknown linetype", "available"),
        ),
        ("layer_table: {version: 2}\nlayers:\n  - {name: FDN, aci: 3}\n", ("version",)),
        (
            "layer_table: {version: 1, undeclared: maybe}\nlayers:\n  - {name: FDN, aci: 3}\n",
            ("error, warn",),
        ),
        (
            "layer_table: {version: 1}\nlayers:\n  - {name: FDN, aci: 3, colour: green}\n",
            ("unknown key",),
        ),
        (
            """
layer_table: {version: 1}
linetypes:
  - {name: SK_BAD, pattern_mm: [-3.0, 2.0]}
layers:
  - {name: FDN, aci: 3}
""",
            ("must start with a dash",),
        ),
        (
            """
layer_table: {version: 1}
linetypes:
  - {name: SK_BAD, pattern_mm: [3.0, 2.0]}
layers:
  - {name: FDN, aci: 3}
""",
            ("at least one gap",),
        ),
        (
            """
layer_table: {version: 1}
linetypes:
  - {name: DASHED, pattern_mm: [3.0, -2.0]}
layers:
  - {name: FDN, aci: 3}
""",
            ("shadows",),
        ),
    ],
)
def test_layer_table_rejects_bad_config(tmp_path, body, substrings):
    path = tmp_path / "bad-layers.yaml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(LayerTableError) as exc:
        load_layer_table(path)

    message = str(exc.value)
    for substring in substrings:
        assert substring in message


def test_layers_show_prints_the_resolved_table(tmp_path, capsys):
    from technical_drawings_for_agents.cli import main

    assert main(["layers", "show", "--layers", "default", "--plot-scale", "200"]) == 0
    captured = capsys.readouterr()
    assert "FDN" in captured.out
    assert "3" in captured.out
    assert "0.35" in captured.out
    assert "CONTINUOUS" in captured.out
    assert "SK_HIDDEN" in captured.out
    assert "3" in captured.out
    assert "0.6" in captured.out

    bad = tmp_path / "bad.yaml"
    bad.write_text("layer_table: {version: 2}\nlayers:\n  - {name: FDN, aci: 3}\n",
                   encoding="utf-8")
    assert main(["layers", "show", "--layers", str(bad)]) == 2
    captured = capsys.readouterr()
    assert "version" in captured.err


def _legacy_build():
    import ezdxf
    from technical_drawings_for_agents.dxf import LAYERS

    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    for name, attribs in LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=attribs.get("color", 7))
    msp = doc.modelspace()
    msp.add_line((0, 0), (1, 0), dxfattribs={"layer": "OUTLINE"})
    msp.add_lwpolyline([(0, 0), (1, 0), (1, 1)], close=True,
                       dxfattribs={"layer": "OBJECT"})
    msp.add_circle((2, 2), 0.5, dxfattribs={"layer": "CENTRE"})
    text = msp.add_text("hello", height=0.25, dxfattribs={"layer": "TEXT", "rotation": 0.0})
    text.set_placement((0, 1))
    return doc


def _run_source(path: Path) -> None:
    """Run a drawing generator the way the CLI does, from its own directory."""

    added = str(path.parent)
    sys.path.insert(0, added)
    try:
        runpy.run_path(str(path), run_name="__main__")
    finally:
        if added in sys.path:
            sys.path.remove(added)


def _volatile_only_difference(left: str, right: str) -> bool:
    """True when two DXF texts differ *only* in lines P3 catalogued as volatile."""

    left_lines = left.replace("\r\n", "\n").splitlines()
    right_lines = right.replace("\r\n", "\n").splitlines()
    if len(left_lines) != len(right_lines):
        return False
    volatile_markers = ("$TDCREATE", "$TDUPDATE", "$FINGERPRINTGUID", "$VERSIONGUID")
    for index, (a, b) in enumerate(zip(left_lines, right_lines)):
        if a == b:
            continue
        # A differing line is acceptable only as the value line of a volatile
        # header variable, or as an ezdxf "<version> @ <timestamp>" marker.
        preceding = left_lines[max(0, index - 2):index]
        if any(marker in line for marker in volatile_markers for line in preceding):
            continue
        if " @ " in a and " @ " in b:
            continue
        return False
    return True


def _linetype_tags(path: Path, name: str) -> dict[int, list[float]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = None
    for index in range(len(lines) - 1):
        if lines[index].strip() == "2" and lines[index + 1].strip() == name:
            start = index
            break
    assert start is not None
    values: dict[int, list[float]] = {40: [], 49: []}
    index = start
    while index < len(lines) - 1:
        code = lines[index].strip()
        value = lines[index + 1].strip()
        if index > start and code == "0":
            break
        if code in {"40", "49"}:
            values[int(code)].append(float(value))
        index += 2
    return values
