"""One entry point for every generator (T1), and backward compatibility (T12).

T12 is the gate: any failure here blocks the change regardless of the rest of the
suite. The new path is reached only through a new module, a new verb, or a flag
that defaults off.
"""

from __future__ import annotations

import ast
import inspect
import json
import shutil
from pathlib import Path

import pytest
import yaml

from technical_drawings_for_agents import bfd
from technical_drawings_for_agents import plot as plot_module
from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.render import (
    dxf_has_inserts,
    find_soffice,
    render_dxf,
    render_py,
    render_source,
    render_svg,
    select_backend,
)

from .synthetic import make_dxf_with_insert

PACKAGE_ROOT = Path(plot_module.__file__).resolve().parent
EXAMPLES = PACKAGE_ROOT / "components" / "examples"
DRAWINGS = PACKAGE_ROOT.parents[1] / "drawings" / "example"

needs_rsvg = pytest.mark.skipif(
    shutil.which("rsvg-convert") is None, reason="rsvg-convert (librsvg) not installed"
)

_BFD_DATA = {
    "meta": {"number": "TST-BFD-001", "title": "Test BFD", "status": "DRAFT"},
    "nodes": [
        {"id": "src", "label": "Source", "type": "source", "elev": 10, "chainage": 0},
        {"id": "p1", "label": "Pond 1", "type": "pond", "elev": 20, "chainage": 500},
        {"id": "snk", "label": "Sink", "type": "sink", "elev": 30, "chainage": 1200},
    ],
    "edges": [{"from": "src", "to": "p1"}, {"from": "p1", "to": "snk"}],
}

_MAP_CONFIG = {
    "map": {
        "crs": "EPSG:32630",
        "extent": [0, 0, 100, 80],
        "layers": [{"name": "pads", "geojson": "pads.geojson"}],
    },
    "meta": {"number": "TST-MAP-001", "title": "Map", "status": "CONCEPT"},
}

_MAP_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[10, 10], [40, 10], [40, 40], [10, 40], [10, 10]]],
            },
        }
    ],
}


class _CountingPlot:
    """Delegates to the real ``plot()`` and records who called it."""

    def __init__(self, real):
        self._real = real
        self.calls: list[Path] = []

    def __call__(self, request):
        self.calls.append(Path(request.source))
        return self._real(request)


@pytest.fixture
def counting_plot(monkeypatch):
    counter = _CountingPlot(plot_module.plot)
    monkeypatch.setattr(plot_module, "plot", counter)
    return counter


# --------------------------------------------------------------------------- #
# T1
# --------------------------------------------------------------------------- #


@needs_rsvg
def test_every_generator_reaches_a_pdf_through_the_one_entry_point(
    tmp_path, counting_plot
):
    """T1 — no generator reaches a PDF by any other route.

    ``plot()`` is monkeypatched with a counting wrapper that delegates to the real
    function, so this asserts routing *and* that the PDFs are genuinely produced.
    """
    work = tmp_path / "work"
    work.mkdir()

    # 1. bfd
    bfd_data = work / "bfd.yaml"
    bfd_data.write_text(yaml.safe_dump(_BFD_DATA), encoding="utf-8")
    assert main(["bfd", str(bfd_data), "--out", str(work / "bfd-out"), "--view", "profile",
                 "--pdf"]) == 0
    assert list((work / "bfd-out").glob("*.pdf"))

    # 2. pid
    pid_src = DRAWINGS / "synthetic-pid" / "SYN-PSK-PID-001.pid.yaml"
    assert main(["pid", str(pid_src), "--out", str(work / "pid-out"), "--pdf"]) == 0
    assert list((work / "pid-out").glob("*.pdf"))

    # 3. component
    assert main(
        [
            "component", str(EXAMPLES / "packaged_unit.yaml"),
            "--emit", "dxf", "--out", str(work / "comp-out" / "unit.dxf"), "--pdf",
        ]
    ) == 0
    assert (work / "comp-out" / "unit.pdf").exists()

    # 4. layout
    layout_out = work / "layout-out" / "layout.dxf"
    assert main(
        [
            "layout", str(EXAMPLES / "site_layout.yaml"),
            "--emit", "dxf", "--out", str(layout_out), "--pdf",
        ]
    ) == 0
    assert layout_out.with_suffix(".pdf").exists()

    # 5. the worked example .py generator, via `plot`
    example = work / "simple-section"
    shutil.copytree(DRAWINGS / "simple-section", example)
    shutil.rmtree(example / "out", ignore_errors=True)
    assert main(["plot", str(example / "source.py"), "--out", str(example / "out")]) == 0
    assert (example / "out" / "EXA-CIV-SEC-001.pdf").exists()

    # 6. map
    map_dir = work / "map"
    map_dir.mkdir()
    (map_dir / "pads.geojson").write_text(json.dumps(_MAP_GEOJSON), encoding="utf-8")
    (map_dir / "plot.yaml").write_text(yaml.safe_dump(_MAP_CONFIG), encoding="utf-8")
    assert main(["map", str(map_dir / "plot.yaml"), "--out", str(map_dir / "out")]) == 0
    assert (map_dir / "out" / "TST-MAP-001.pdf").exists()

    # Every one of the six went through plot(). The .py generator plots both the
    # SVG and the DXF it wrote, so the call count exceeds the generator count.
    called = {path.suffix for path in counting_plot.calls}
    assert called <= {".svg", ".dxf", ".py"}
    assert len(counting_plot.calls) >= 6, counting_plot.calls


CONVERTER_TOKENS = ("rsvg-convert", "soffice", "cairosvg", "pdfunite", "pdftoppm")

#: Every file allowed to mention a converter at all, and why. Anything else naming
#: one has grown a second conversion path, which is the defect T1 exists to catch.
_MENTION_ALLOWED = {
    "plot.py": "owns the SVG->PDF/PNG chain",
    "render.py": "the frozen legacy path",
    "cli.py": "flag help text only — asserted below to invoke nothing",
    "backdrop.py": "P8: documents librsvg's resource policy in comments",
}


def test_no_module_but_plot_and_render_names_a_conversion_backend():
    """T1, routing half — asserted over the tree, so a second shell-out cannot creep in."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if path.name in _MENTION_ALLOWED:
            continue
        source = path.read_text(encoding="utf-8")
        hits = [token for token in CONVERTER_TOKENS if token in source]
        if hits:
            offenders[str(path.relative_to(PACKAGE_ROOT))] = hits
    assert offenders == {}, offenders


@pytest.mark.parametrize("name", ["cli.py", "backdrop.py"])
def test_the_files_that_only_mention_a_converter_never_execute_one(name):
    """The stronger half: a *mention* is help text or a comment; a *call* is a backend.

    ``cli.py`` names rsvg/cairosvg/pdftoppm in ``--svg-backend`` choices and
    ``--ink-check`` help; ``backdrop.py`` (P8) documents librsvg's resource policy.
    Neither may import a rasteriser or spawn a process.
    """
    tree = ast.parse((PACKAGE_ROOT / name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in ("subprocess", "cairosvg"), alias.name
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("subprocess", "cairosvg")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name):
                assert not (owner.id == "subprocess" or owner.id == "os" and node.func.attr
                            in ("system", "popen", "execv")), ast.dump(node.func)


def test_the_plot_module_has_exactly_one_error_class():
    """House style: one module-specific error class, in the shape of ``LayoutError``."""
    tree = ast.parse(Path(plot_module.__file__).read_text(encoding="utf-8"))
    classes = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name) and base.id.endswith("Error") for base in node.bases
        )
    ]
    assert classes == ["PlotError"]
    assert issubclass(plot_module.PlotError, RuntimeError)


# --------------------------------------------------------------------------- #
# T12 — backward compatibility. Any failure here blocks the change.
# --------------------------------------------------------------------------- #

#: Recorded as literals so a signature change is a test failure, not a surprise.
_FROZEN_SIGNATURES = {
    render_dxf: "(dxf_path: 'str | Path', out_dir: 'str | Path', stem: 'str | None' = None, "
    "backend: 'str' = 'auto', policy=None) -> 'list[Path]'",
    render_svg: "(svg_path: 'str | Path', out_dir: 'str | Path', stem: 'str | None' = None, "
    "policy=None) -> 'list[Path]'",
    render_py: "(py_path: 'str | Path', out_dir: 'str | Path', policy=None) -> 'list[Path]'",
    render_source: "(source: 'str | Path', out_dir: 'str | Path', policy=None) -> 'list[Path]'",
    select_backend: "(has_inserts: 'bool', soffice_available: 'bool') -> 'str'",
    dxf_has_inserts: "(doc) -> 'bool'",
    find_soffice: "() -> 'str | None'",
}


def test_the_legacy_render_signatures_are_unchanged():
    """T12.1."""
    for function, expected in _FROZEN_SIGNATURES.items():
        assert str(inspect.signature(function)) == expected, function.__name__


def test_the_degrade_to_matplotlib_routing_rule_still_holds():
    """T12.2 — ``test_render_routing.py:25``'s rule, re-asserted from the new suite.

    It is *measurably the better* backend (spec §2.3), but the rule is frozen either
    way: changing it would change an existing caller's output.
    """
    assert select_backend(True, False) == "matplotlib"
    assert select_backend(True, True) == "libreoffice"
    assert select_backend(False, True) == "matplotlib"
    assert select_backend(False, False) == "matplotlib"


def test_the_worked_example_gains_no_new_files_in_out(tmp_path):
    """T12.3 — the exact set of filenames ``render`` produces, recorded as a literal."""
    work = tmp_path / "simple-section"
    shutil.copytree(DRAWINGS / "simple-section", work)
    shutil.rmtree(work / "out", ignore_errors=True)

    render_source(work / "source.py", work / "out")
    assert {path.name for path in (work / "out").iterdir()} == {
        "EXA-CIV-SEC-001.svg",
        "EXA-CIV-SEC-001.dxf",
        "EXA-CIV-SEC-001.pdf",
        "EXA-CIV-SEC-001.png",
    }


def test_render_dxf_with_inserts_and_no_soffice_still_warns_and_returns(
    tmp_path, monkeypatch, caplog
):
    """T12.4 — the existing degrade-with-a-warning path is untouched."""
    monkeypatch.setattr("technical_drawings_for_agents.render.find_soffice", lambda: None)
    src = make_dxf_with_insert(tmp_path / "ins.dxf")
    with caplog.at_level("WARNING", logger="technical_drawings_for_agents.render"):
        outputs = render_dxf(src, tmp_path / "out")
    assert {path.suffix for path in outputs} == {".pdf", ".png"}
    assert any("block INSERT" in record.getMessage() for record in caplog.records)


def test_plain_render_still_prints_the_existing_shape(tmp_path, capsys):
    """T12.5 — stdout for ``render`` with no new flags is byte-shape identical."""
    src = make_dxf_with_insert(tmp_path / "ins.dxf")
    assert main(["render", str(src), "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Rendered 2 artifact(s) into ")
    assert "PLOT " not in out


def test_bfd_without_pdf_produces_no_pdf(tmp_path):
    """T12.6 — no new file appears in out/ unless asked for."""
    data = tmp_path / "bfd.yaml"
    data.write_text(yaml.safe_dump(_BFD_DATA), encoding="utf-8")
    out = tmp_path / "out"
    assert main(["bfd", str(data), "--out", str(out), "--view", "profile"]) == 0
    assert list(out.glob("*.pdf")) == []

    outputs = bfd.build(data, tmp_path / "out2", view="profile")
    assert outputs
    assert all(path.suffix == ".svg" for path in outputs)


def test_the_deprecation_warning_does_not_change_the_artifacts(tmp_path):
    """§4.1 — a warning is not a behaviour change. Only emitted on the auto route."""
    if find_soffice() is None:
        pytest.skip("LibreOffice (soffice) not installed")
    src = make_dxf_with_insert(tmp_path / "ins.dxf")
    with pytest.warns(DeprecationWarning, match="technical_drawings_for_agents plot"):
        auto = render_dxf(src, tmp_path / "auto")
    assert {path.suffix for path in auto} == {".pdf", ".png"}
    # An explicit backend request is the caller's choice; it is not deprecated.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        render_dxf(src, tmp_path / "forced", backend="libreoffice")


@needs_rsvg
def test_render_plot_is_opt_in_and_routes_through_plot(tmp_path, counting_plot, capsys):
    """``render --plot`` reaches the checked path; plain ``render`` never does."""
    src = make_dxf_with_insert(tmp_path / "ins.dxf")
    assert main(["render", str(src), "--out", str(tmp_path / "plot-out"), "--plot"]) == 0
    assert counting_plot.calls == [src]
    assert "PLOT pdf" in capsys.readouterr().out

    counting_plot.calls.clear()
    assert main(["render", str(src), "--out", str(tmp_path / "legacy-out")]) == 0
    assert counting_plot.calls == []


# --------------------------------------------------------------------------- #
# the CLI contract
# --------------------------------------------------------------------------- #


@needs_rsvg
def test_json_output_is_exactly_one_object_on_stdout(tmp_path, capsys):
    src = make_dxf_with_insert(tmp_path / "ins.dxf")
    assert main(["plot", str(src), "--out", str(tmp_path / "out"), "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # raises if anything else was printed
    assert payload["ok"] is True
    assert payload["exit"] == 0
    assert payload["results"][0]["svg_backend"] == "rsvg"
    assert payload["results"][0]["state"] == "ok"
    assert payload["results"][0]["fidelity"]["ok"] is True


def test_json_output_on_failure_still_emits_one_object(tmp_path, capsys, monkeypatch):
    from .synthetic import make_dxf_block_heavy
    from .test_plot_fidelity import _CircleDroppingRecorder

    monkeypatch.setattr(plot_module, "Recorder", _CircleDroppingRecorder)
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    assert main(["plot", str(src), "--out", str(tmp_path / "out"), "--json"]) == 3
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["exit"] == 3
    assert payload["error"]["kind"] == "fidelity"
    assert "error: " in captured.err


def test_a_missing_source_is_exit_2(tmp_path, capsys):
    assert main(["plot", str(tmp_path / "absent.dxf")]) == 2
    assert "source not found" in capsys.readouterr().err
