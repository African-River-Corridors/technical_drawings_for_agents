"""DXF from a drawing document and its normalised content (clause 15.4).

Model space holds the entities of the viewports in model metres. Paper space (the layout ``SHEET``)
holds the sheet furniture and the marks of the hatches in paper millimetres, and one viewport for
each viewport of the document, at the stated scale. Every entity carries XDATA of the application
``TDFA`` so that a reader can make the normalised content again (see ``tests/dxf_reader.py``).
"""

from __future__ import annotations

import io
from contextlib import contextmanager

import ezdxf
from ezdxf.enums import TextEntityAlignment as Align

from . import resources, rules, symbols
from .numbers import round_half_even
from .pipeline import FURNITURE_LAYER, colour_table, declared_layers, entity_style

APPID = "TDFA"
LAYOUT = "SHEET"
TEXT_STYLE = "TDFA"
VIEWPORT_LAYER = "VIEWPORT"
FONT_FILE_NAME = "BarlowSemiCondensed-Regular.ttf"

ALIGN = {
    ("left", "baseline"): Align.LEFT,
    ("centre", "baseline"): Align.CENTER,
    ("right", "baseline"): Align.RIGHT,
    ("left", "middle"): Align.MIDDLE_LEFT,
    ("centre", "middle"): Align.MIDDLE_CENTER,
    ("right", "middle"): Align.MIDDLE_RIGHT,
    ("left", "top"): Align.TOP_LEFT,
    ("centre", "top"): Align.TOP_CENTER,
    ("right", "top"): Align.TOP_RIGHT,
}


@contextmanager
def _fixed_metadata():
    """ezdxf stamps the clock and random ids. The standard asks for repeat builds that agree."""
    saved = ezdxf.options.write_fixed_meta_data_for_testing
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        yield
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = saved


def linetype_name(line_type: str, width_mm: float) -> str:
    """15.4.4: one DXF line type for each pair of standard line type and width."""
    if line_type == "continuous":
        return "CONTINUOUS"
    return f"TDFA_{line_type.upper()}_{round(width_mm * 100):03d}"


def lineweight(width_mm: float) -> int:
    """8.3.4: hundredths of a millimetre."""
    return round(width_mm * 100)


def _ensure_linetype(doc, line_type: str, width_mm: float) -> str:
    name = linetype_name(line_type, width_mm)
    if name not in doc.linetypes:
        pattern = rules.line_pattern(line_type, width_mm)
        doc.linetypes.add(name, pattern=[sum(abs(v) for v in pattern), *pattern], description=f"{line_type}, line width {width_mm} mm")
    return name


def _xdata(entity, **tags):
    items = [(1000, f"{key}={value}") for key, value in tags.items() if value is not None]
    entity.set_xdata(APPID, items)


def _attribs(layer, line_type, width, doc):
    return {"layer": layer, "linetype": _ensure_linetype(doc, line_type, width), "lineweight": lineweight(width)}


def _hatch(space, boundary, holes, solid, attribs, xdata):
    hatch = space.add_hatch(color=256, dxfattribs={"layer": attribs["layer"], "lineweight": attribs.get("lineweight", -1)})
    hatch.set_solid_fill()
    hatch.paths.add_polyline_path([tuple(p) for p in boundary], is_closed=True)
    for hole in holes:
        hatch.paths.add_polyline_path([tuple(p) for p in hole], is_closed=True)
    if not solid:
        hatch.dxf.invisible = 1  # a marked hatch draws only its marks (10.5.8)
    _xdata(hatch, **xdata)
    return hatch


def _text(space, text, anchor, height, rotation, align_h, align_v, layer, xdata):
    entity = space.add_text(text, height=height, rotation=rotation, dxfattribs={"layer": layer, "style": TEXT_STYLE})
    entity.set_placement(tuple(anchor), align=ALIGN[(align_h, align_v)])
    _xdata(entity, **xdata)
    return entity


def window_overlap_warnings(doc) -> list[str]:
    """15.4.9: two viewports share one model space, so overlapping model windows show each other's content."""
    out = []
    vps = doc["viewports"]
    for i in range(len(vps)):
        for j in range(i + 1, len(vps)):
            a, b = vps[i]["window"], vps[j]["window"]
            ox = min(a["xmax_m"], b["xmax_m"]) - max(a["xmin_m"], b["xmin_m"])
            oy = min(a["ymax_m"], b["ymax_m"]) - max(a["ymin_m"], b["ymin_m"])
            if ox > 0 and oy > 0:
                out.append(f"15.4.9: the model windows of viewports {vps[i]['id']} and {vps[j]['id']} overlap")
    return out


def dxf_from_document(document: dict, norm: dict, base_dir=None) -> tuple[str, list[str]]:
    """Return the DXF text and the warnings."""
    sheet = norm["sheet"]
    height_sheet = sheet["height_mm"]
    _, group = rules.line_group(sheet["size"])
    declared = declared_layers(document)
    libs, _ = symbols.load_libraries(document, base_dir)
    with _fixed_metadata():
        doc = ezdxf.new("R2018", setup=True)
        doc.header["$INSUNITS"] = 6  # model space is in metres
        doc.header["$MEASUREMENT"] = 1
        doc.header["$PSLTSCALE"] = 1  # line patterns follow the paper, not the viewport scale
        doc.header["$LWDISPLAY"] = 1
        doc.appids.add(APPID)
        doc.styles.add(TEXT_STYLE, font=FONT_FILE_NAME)
        styles = {row["name"]: row for row in resources.line_styles()["layers"]}
        layers = dict(declared)
        layers.setdefault(FURNITURE_LAYER, (styles[FURNITURE_LAYER]["role"], styles[FURNITURE_LAYER]["line_type"], styles[FURNITURE_LAYER]["aci"]))
        colours = colour_table(document)  # 8.6.5: empty under the monochrome profile
        for name, (role, line_type, aci) in layers.items():
            width = group[role]
            layer = doc.layers.add(name, color=aci, lineweight=lineweight(width), linetype=_ensure_linetype(doc, line_type, width))
            if name in colours:  # 15.4.12: the print colour is the true colour of the layer; all members in XDATA
                c = colours[name]
                if "print_colour" in c:
                    layer.rgb = tuple(int(c["print_colour"][i : i + 2], 16) for i in (1, 3, 5))
                _xdata(layer, **c)
        doc.layers.add(VIEWPORT_LAYER, color=7, plot=False)

        msp = doc.modelspace()
        by_vp = {vp["id"]: vp for vp in document["viewports"]}
        scale = {vp["id"]: vp["scale"] for vp in norm["viewports"]}
        for vp in document["viewports"]:
            n = scale[vp["id"]]
            rotation = vp.get("rotation_deg", 0.0) % 360.0
            for e in vp["entities"]:
                if e["type"] in ("dimension", "leader"):
                    continue  # their parts are paper-space entities (12.7, 15.4.5)
                if e["type"] == "image":
                    _image(doc, msp, e, vp["id"], norm)
                    continue
                if e["type"] == "symbol":
                    _symbol_insert(doc, msp, e, vp["id"], n, rotation, declared, group, libs)
                    continue
                _model_entity(doc, msp, e, vp["id"], n, rotation, declared, group)

        layout = doc.layouts.new(LAYOUT)
        doc.layouts.delete("Layout1")  # the default layout; the sheet is the only one
        layout.page_setup(size=(sheet["width_mm"], sheet["height_mm"]), margins=(0, 0, 0, 0), units="mm")
        flip = lambda p: (p[0], height_sheet - p[1])  # noqa: E731  paper is y down in the standard, y up in DXF
        for e in norm["entities"]:
            origin = e["origin"]
            if origin.startswith("viewport.") and not origin.endswith((".scale_bar", ".north_arrow", ".hatch", ".dimension", ".leader", ".backing")):
                continue
            _paper_entity(doc, layout, e, flip)
        for vp in norm["viewports"]:
            f = vp["frame"]
            src = by_vp[vp["id"]]
            w = src["window"]
            viewport = layout.add_viewport(
                center=(f["x_mm"] + f["width_mm"] / 2, height_sheet - (f["y_mm"] + f["height_mm"] / 2)),
                size=(f["width_mm"], f["height_mm"]),
                view_center_point=((w["xmin_m"] + w["xmax_m"]) / 2, (w["ymin_m"] + w["ymax_m"]) / 2),
                view_height=f["height_mm"] * vp["scale"] / 1000,
                dxfattribs={"layer": VIEWPORT_LAYER, "view_twist_angle": src.get("rotation_deg", 0.0) % 360.0},
            )
            _xdata(viewport, id=vp["id"], scale=vp["scale"])
        stream = io.StringIO()
        doc.write(stream)
    return stream.getvalue(), window_overlap_warnings(document)


def _model_entity(doc, msp, e, vp_id, n, rotation, declared, group):
    t = e["type"]
    line_type, width = entity_style(e, declared, group)
    attribs = _attribs(e["layer"], line_type, width, doc)
    tags = {"vp": vp_id, "id": e.get("id"), "type": t}
    if t in ("line", "centre_line"):
        entity = msp.add_line(tuple(e["from_m"]), tuple(e["to_m"]), dxfattribs=attribs)
    elif t == "polyline":
        entity = msp.add_lwpolyline([tuple(p) for p in e["points_m"]], close=e.get("closed", False), dxfattribs=attribs)
    elif t == "circle":
        entity = msp.add_circle(tuple(e["centre_m"]), e["radius_m"], dxfattribs=attribs)
    elif t == "arc":
        entity = msp.add_arc(tuple(e["centre_m"]), e["radius_m"], e["start_deg"] % 360.0, e["end_deg"] % 360.0, dxfattribs=attribs)
    elif t == "hatch":
        solid = e["pattern"] == "solid"
        _hatch(msp, e["boundary_m"], e.get("holes_m", []), solid, {"layer": e["layer"], "lineweight": lineweight(width)},
               {**tags, "pattern": e["pattern"], "angle": e.get("angle_deg", 0)})
        return
    else:  # text
        height = e["height_mm"] * n / 1000  # a text of h mm on the paper, in model metres
        model_rotation = (e.get("rotation_deg", 0.0) - rotation) % 360.0
        _text(msp, e["text"], e["anchor_m"], height, model_rotation, e.get("align_h", "left"), e.get("align_v", "baseline"), e["layer"], {**tags, "paper_height": e["height_mm"]})
        return
    _xdata(entity, **tags)


def _image(doc, msp, e, vp_id, norm):
    """15.4.12: an IMAGE in model space over its model box, linked to the file by the path of the document."""
    item = next(x for x in norm["entities"] if x["type"] == "image" and x["origin"] == f"viewport.{vp_id}" and x["sha256"] == e["sha256"]
                and x.get("id") == e.get("id"))
    w_px, h_px = item["size_px"]
    x0, y0, x1, y1 = e["bbox_m"]
    image_def = doc.add_image_def(filename=e["path"], size_in_pixel=(w_px, h_px), name=f"TDFA_{e['sha256'][:16]}")
    entity = msp.add_image(image_def, insert=(x0, y0), size_in_units=(x1 - x0, y1 - y0), rotation=0, dxfattribs={"layer": e["layer"]})
    _xdata(entity, vp=vp_id, id=e.get("id"), type="image", sha256=e["sha256"], components=item["components"], opacity=item["opacity"])


def block_name(library, symbol, layer, role_override):
    """15.4.10."""
    return f"TDFA_{library}_{symbol}@{layer}" + (f"~{role_override}" if role_override else "")


def _symbol_insert(doc, msp, e, vp_id, n, rotation, declared, group, libs):
    """15.4.10: a BLOCK for the symbol on this layer, an INSERT, and the label as a TEXT."""
    symbol = libs[e["library"]][e["symbol"]]
    name = block_name(e["library"], e["symbol"], e["layer"], e.get("role"))
    layer_role, layer_lt, _ = declared[e["layer"]]
    if name not in doc.blocks:
        origin = symbol.get("insertion", [0, 0])
        block = doc.blocks.new(name, base_point=tuple(origin))
        for part in symbol["geometry"]:
            role = part.get("role") or e.get("role") or layer_role
            width = group[role]
            line_type = part.get("line_type") or layer_lt
            attribs = _attribs(e["layer"], line_type, width, doc)
            t = part["type"]
            if t == "line":
                block.add_line(tuple(part["from"]), tuple(part["to"]), dxfattribs=attribs)
            elif t == "polyline":
                block.add_lwpolyline([tuple(p) for p in part["points"]], close=part.get("closed", False), dxfattribs=attribs)
            elif t == "circle":
                block.add_circle(tuple(part["centre"]), part["radius"], dxfattribs=attribs)
            elif t == "arc":
                block.add_arc(tuple(part["centre"]), part["radius"], part["start_deg"] % 360.0, part["end_deg"] % 360.0, dxfattribs=attribs)
            else:
                _hatch(block, part["boundary"], [], True, {"layer": e["layer"], "lineweight": lineweight(width)}, {"type": "hatch", "pattern": "solid", "angle": 0})
    placement = symbols.Placement(e, symbol, n)
    ref = msp.add_blockref(
        name, tuple(e["position_m"]),
        dxfattribs={"xscale": placement.factor, "yscale": placement.factor, "zscale": placement.factor, "rotation": placement.rotation, "layer": e["layer"]},
    )
    _xdata(ref, vp=vp_id, id=e.get("id"), type="symbol")
    if "label" in e:
        slot = symbol["label_slot"]
        at = placement.model(slot["at"])
        _text(msp, e["label"], at, slot.get("height_mm", 3.5) * n / 1000, (0.0 - rotation) % 360.0, slot.get("align_h", "centre"), slot.get("align_v", "middle"),
              e["layer"], {"vp": vp_id, "type": "text", "part": "symbol", "paper_height": slot.get("height_mm", 3.5)})


def _paper_entity(doc, layout, e, flip):
    t = e["type"]
    tags = {"origin": e["origin"], "id": e.get("id"), "type": t}
    if e["origin"].endswith(".backing"):
        tags["print_colour"] = e["print_colour"]  # 8.6.7: a plate is not coloured by its layer
    if t == "text":
        _text(layout, e["text"], flip(e["anchor_mm"]), e["height_mm"], e["rotation_deg"], e["align_h"], e["align_v"], e["layer"], tags)
        return
    if t == "hatch":
        _hatch(layout, [flip(p) for p in e["boundary_mm"]], [[flip(p) for p in h] for h in e["holes_mm"]], e["pattern"] == "solid",
               {"layer": e["layer"], "lineweight": lineweight(e["width_mm"])}, {**tags, "pattern": e["pattern"], "angle": e["angle_deg"]})
        return
    attribs = _attribs(e["layer"], e["line_type"], e["width_mm"], doc)
    if t == "line":
        entity = layout.add_line(flip(e["from_mm"]), flip(e["to_mm"]), dxfattribs=attribs)
    elif t == "polyline":
        entity = layout.add_lwpolyline([flip(p) for p in e["points_mm"]], close=e["closed"], dxfattribs=attribs)
    elif t == "circle":
        entity = layout.add_circle(flip(e["centre_mm"]), e["radius_mm"], dxfattribs=attribs)
    elif t == "arc":  # the angle is a visual counter-clockwise angle, which the flip of y keeps
        entity = layout.add_arc(flip(e["centre_mm"]), e["radius_mm"], e["start_deg"], (e["start_deg"] + e["sweep_deg"]) % 360.0, dxfattribs=attribs)
    else:
        raise ValueError(f"no paper-space form for {t}")
    _xdata(entity, **tags)


_ = round_half_even
