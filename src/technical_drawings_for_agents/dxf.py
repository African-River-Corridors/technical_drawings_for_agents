"""ezdxf-based DXF exporter.

Writes geometry in **real-world metres** (model space) — the same coordinate
space the SVG :class:`~technical_drawings_for_agents.svg.ViewBox` maps from — so the DXF handed
to a drafter / imported into AutoCAD is unit-true. ASCII DXF (R2018 by
default) is the text-source interchange the standard calls for.

Layers follow a lightweight convention (OUTLINE / DIMENSIONS / CENTRE / HATCH /
TEXT / TITLEBLOCK / WATERMARK). Real supplier tags are preserved on read;
this exporter only writes what the generator produced — it never invents
geometry.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import ezdxf
import ezdxf.lldxf.const

from .layers import ApplyReport, LayerSpec, LayerTable
from .provenance import ProvenanceError

# Undeclared-layer warnings are a layer-table concern, so they are logged on the
# layers logger a caller already configures for the loader's own warnings.
_LOG = logging.getLogger("technical_drawings_for_agents.layers")

LAYERS = {
    "OUTLINE": {"color": 7},       # white/black
    "OBJECT": {"color": 8},        # grey
    "DIMENSIONS": {"color": 3},    # green
    "CENTRE": {"color": 6},        # magenta
    "HATCH": {"color": 9},
    "TEXT": {"color": 7},
    "TITLEBLOCK": {"color": 7},
    "WATERMARK": {"color": 1},     # red
}


class DxfBuilder:
    """Collects real-world (metre) geometry and writes an ASCII DXF.

    Methods take real-world coordinates; nothing is scaled. Layer names default
    to the convention above. Passing a layer table opts into declared-layer
    validation and layer-record lineweight/linetype output.
    """

    def __init__(
        self,
        dxfversion: str = "R2018",
        units: str = "m",
        policy=None,
        layer_table: LayerTable | None = None,
        plot_scale: float | None = None,
    ):
        self.policy = policy
        self.layer_table = layer_table
        self.plot_scale = plot_scale
        self._warned_undeclared_layers: set[str] = set()
        # ezdxf stamps TWO marker XRECORDs with the wall clock, at two different
        # moments: CREATED_BY_EZDXF in Drawing._create_ezdxf_metadata() during
        # ezdxf.new(), and WRITTEN_BY_EZDXF in _update_metadata() during saveas().
        # Scoping options.write_fixed_meta_data_for_testing to the save alone
        # therefore leaves the *creation* marker carrying the clock — one
        # differing line per rebuild, which is enough to defeat byte-identity.
        # So the option is toggled around document creation as well, and restored
        # immediately each time: it stays a process-global we never leak.
        with _fixed_dxf_metadata(policy):
            self.doc = ezdxf.new(dxfversion, setup=True)
        # DXF unit code 6 == metres.
        self.doc.header["$INSUNITS"] = 6 if units == "m" else 0
        self.msp = self.doc.modelspace()
        if layer_table is None:
            for name, attribs in LAYERS.items():
                if name not in self.doc.layers:
                    self.doc.layers.add(name, color=attribs.get("color", 7))
            self.layer_report = ApplyReport()
        else:
            self.layer_report = layer_table.apply(self.doc, plot_scale=plot_scale)

    # -- primitives -------------------------------------------------------
    def line(self, p1, p2, layer="OUTLINE"):
        layer = self._checked_layer(layer)
        self.msp.add_line(self._point_m(p1), self._point_m(p2), dxfattribs={"layer": layer})
        return self

    def polyline(self, points, closed=False, layer="OUTLINE"):
        layer = self._checked_layer(layer)
        self.msp.add_lwpolyline(
            [self._point_m(point) for point in points],
            close=closed,
            dxfattribs={"layer": layer},
        )
        return self

    def circle(self, center, radius, layer="OUTLINE"):
        layer = self._checked_layer(layer)
        self.msp.add_circle(
            self._point_m(center),
            self._m(radius),
            dxfattribs={"layer": layer},
        )
        return self

    def text(self, position, value, height=0.25, layer="TEXT", rotation=0.0):
        layer = self._checked_layer(layer)
        entity = self.msp.add_text(
            str(value),
            height=self._m(height),
            dxfattribs={"layer": layer, "rotation": self._deg(rotation)},
        )
        entity.set_placement(self._point_m(position))
        return self

    def linear_dim(self, p1, p2, distance=1.0, layer="DIMENSIONS", text_height=0.18):
        """Add a native AutoCAD linear dimension between two real points.

        Overrides the dimstyle so the measurement reads true metres (unit
        length factor 1.0, 2 decimals) with a text/arrow size sensible at
        metre scale — the packaged EZDXF dimstyle is tuned for mm drawings.
        """
        layer = self._checked_layer(layer)
        text_height = self._m(text_height)
        dim = self.msp.add_linear_dim(
            base=self._point_m((p1[0], p1[1] + distance)),
            p1=self._point_m(p1),
            p2=self._point_m(p2),
            dxfattribs={"layer": layer},
            override={
                "dimlfac": 1.0,   # length factor: report true drawing units (m)
                "dimdec": 2,      # 2 decimal places
                "dimtxt": text_height,
                "dimasz": text_height,   # arrow size
                "dimexe": self._m(text_height * 0.6),
                "dimexo": self._m(text_height * 0.3),
            },
        )
        dim.render()
        return self

    # -- blocks / inserts -------------------------------------------------
    @staticmethod
    def read_dxf(path: str | Path):
        """Read an existing DXF into an ezdxf document.

        Uses ``ezdxf.readfile``; falls back to ``ezdxf.recover.readfile`` on a
        malformed file. This is the read side that lets a cleaned/ingested
        vendor drawing be reused (e.g. inserted as a layout block).
        """
        path = Path(path)
        try:
            return ezdxf.readfile(path)
        except Exception:  # noqa: BLE001
            from ezdxf import recover

            doc, _auditor = recover.readfile(path)
            return doc

    def add_block_from_dxf(self, dxf_path: str | Path, name: str | None = None) -> str:
        """Import an external DXF's modelspace as a reusable block definition.

        Returns the block name. Pair with :meth:`insert` to place the cleaned
        vendor drawing into our layout at a point/scale/rotation. Geometry is
        copied faithfully (nothing is redrawn).
        """
        src = self.read_dxf(dxf_path)
        base = name or Path(dxf_path).stem
        # Sanitise + de-duplicate the block name.
        base = "".join(c for c in base if c.isalnum() or c in ("_", "-")) or "BLOCK"
        block_name = base
        i = 1
        while block_name in self.doc.blocks:
            block_name = f"{base}_{i}"
            i += 1

        block = self.doc.blocks.new(name=block_name)
        for entity in src.modelspace():
            try:
                block.add_foreign_entity(entity, copy=True)
            except Exception:  # noqa: BLE001
                # Entity type not importable in isolation — skip it (geometry
                # types round-trip; exotic annotation resources may not).
                continue
        return block_name

    def insert(self, name: str, point, scale: float = 1.0, rotation: float = 0.0,
               layer: str = "OBJECT"):
        """Place a block reference (INSERT) at ``point`` with scale/rotation."""
        layer = self._checked_layer(layer)
        self.msp.add_blockref(
            name,
            self._point_m(point),
            dxfattribs={
                "layer": layer,
                "xscale": self._ratio(scale),
                "yscale": self._ratio(scale),
                "zscale": self._ratio(scale),
                "rotation": self._deg(rotation),
            },
        )
        return self

    def provenance_stamp(self, text: str, position, height=0.12):
        """Add a provenance stamp on the existing TITLEBLOCK layer."""

        return self.text(position, text, height=height, layer="TITLEBLOCK")

    def _checked_layer(self, layer: str) -> str:
        if self.layer_table is None:
            return layer
        if self.layer_table.has(layer):
            return self.layer_table.get(layer).name
        if self.layer_table.undeclared == "error":
            self.layer_table.get(layer)
        return self._auto_create_undeclared_layer(layer)

    def _auto_create_undeclared_layer(self, layer: str) -> str:
        assert self.layer_table is not None
        if layer not in self._warned_undeclared_layers:
            _LOG.warning(
                "layer %r is not declared in the layer table; auto-creating it because "
                "layer_table.undeclared is 'warn'",
                layer,
            )
            self._warned_undeclared_layers.add(layer)
        created = False
        if layer not in self.doc.layers:
            fallback = LayerSpec(
                name=layer,
                aci=7,
                lineweight_mm=self.layer_table.default_lineweight_mm,
                linetype="CONTINUOUS",
                description=f"Undeclared layer auto-created from {_layer_source(self.layer_table)}",
            )
            record = self.doc.layers.add(
                layer,
                color=fallback.aci,
                lineweight=fallback.dxf_lineweight,
                linetype=fallback.linetype,
                plot=True,
            )
            record.description = fallback.description
            created = True
        undeclared = self.layer_report.undeclared
        if layer not in undeclared:
            undeclared = (*undeclared, layer)
        self.layer_report = ApplyReport(
            layers_created=(
                (*self.layer_report.layers_created, layer)
                if created
                else self.layer_report.layers_created
            ),
            layers_updated=self.layer_report.layers_updated,
            linetypes_created=self.layer_report.linetypes_created,
            lineweights_snapped=self.layer_report.lineweights_snapped,
            undeclared=undeclared,
        )
        return layer

    # -- output -----------------------------------------------------------
    def save(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _fixed_dxf_metadata(self.policy):
            if self.policy is not None:
                _canonicalise_class_registry(self.doc)
            self.doc.saveas(path)
        return path

    def _m(self, value: float) -> float:
        if self.policy is None:
            return value
        return self.policy.q(value, self.policy.precision_m)

    def _deg(self, value: float) -> float:
        if self.policy is None:
            return value
        return self.policy.q(value, self.policy.precision_deg)

    def _ratio(self, value: float) -> float:
        if self.policy is None:
            return value
        return self.policy.q(value, self.policy.precision_ratio)

    def _point_m(self, point) -> Any:
        if self.policy is None:
            return point
        return tuple(self._m(value) for value in point)


def _canonicalise_class_registry(doc) -> None:
    """Sort the CLASSES section, which ezdxf populates by iterating a **set**.

    ``ClassesSection.add_required_classes()`` ends with::

        for dxftype in self.doc.entitydb.dxf_types_in_use():
            self.add_class(dxftype)

    and ``dxf_types_in_use()`` returns a ``set``, so the order CLASS records land
    in the section varies with ``PYTHONHASHSEED`` *and* with the set's insertion
    history. Two machines therefore write the same drawing with two different
    CLASSES orderings, and the bytes differ — measured here as ``LAYOUT`` and
    ``ACDBPLACEHOLDER`` swapping places. This is exactly the unordered-container
    defect the spec catalogues in our own code, sitting in a dependency instead,
    and it is why fixed metadata alone is not sufficient for byte-identity.

    CLASSES ordering carries no drawing information — it is a class registry that
    readers load into a map, and ezdxf itself already emits it in an arbitrary
    order — so sorting it is safe, and it *makes* the bytes reproducible rather
    than merely comparable. Opt-in only: this runs under a policy and never on
    the default path.

    ``add_required_classes`` is invoked here (it is idempotent: ``register()``
    skips keys already present) so that ``saveas``'s own later call appends
    nothing after the sort. ``commit_pending_changes`` runs first for the same
    reason — it may create entities, and hence classes.
    """

    doc.commit_pending_changes()
    if doc.dxfversion > ezdxf.lldxf.const.DXF12:
        doc.classes.add_required_classes(doc.dxfversion)
    registry = doc.classes.classes
    ordered = sorted(registry.items())
    registry.clear()
    registry.update(ordered)


def _layer_source(table: LayerTable) -> str:
    return str(table.source) if table.source is not None else "<in-memory>"


@contextmanager
def _fixed_dxf_metadata(policy):
    if policy is None or not policy.fixed_dxf_metadata:
        yield
        return
    attr = "write_fixed_meta_data_for_testing"
    if not hasattr(ezdxf.options, attr):
        version = getattr(ezdxf, "__version__", "unknown")
        raise ProvenanceError(
            f"this ezdxf build ({version}) has no options.{attr}; a reproducible DXF "
            "cannot be produced — pin ezdxf>=1.1"
        )
    previous = getattr(ezdxf.options, attr)
    setattr(ezdxf.options, attr, True)
    try:
        yield
    finally:
        setattr(ezdxf.options, attr, previous)
