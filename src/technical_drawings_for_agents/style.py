"""Style tokens for engineering-drawing SVG output.

Project-agnostic colour, line-weight and dash tokens. These are plain SVG
colours so the helpers work in standalone generated files. When an SVG is
embedded in an HTML report, callers may pass CSS variables (e.g.
``stroke="var(--cad-outline)"``) to the helpers instead to adopt the host
theme.

Generalised from an earlier vessel-design ``svg_utils.py``: the vessel/
barge-specific colours (hull, tank, tug) are intentionally dropped — only the
generic CAD drawing tokens are kept here.
"""

# --- Sheet / paper ---
COL_CAD_BG = "#1a1a2e"
COL_CAD_OUTLINE = "#d9e2ec"
COL_CAD_OBJECT = "#b8c7d9"
COL_CAD_DIM = "#888888"
COL_CAD_DIM_TEXT = "#bbbbbb"
COL_CAD_CENTER = "#ff66cc"
COL_CAD_TEXT = "#cccccc"
COL_CAD_WARN = "#ff4444"

# --- Hatch fill colours ---
COL_CAD_HATCH = "#8996a8"
COL_CAD_CONCRETE = "#9aa0b4"
COL_CAD_ROCK = "#c8873c"
COL_CAD_SOIL = "#8a7350"
COL_CAD_WATER = "#4488ff"

# --- Line weights (SVG px at sheet scale) ---
# Layer-table pens live in technical_drawings_for_agents.layers and are opt-in; these pixel
# tokens stay for legacy callers that still pass explicit SVG styles.
LW_OUTLINE_THICK = 1.8
LW_OUTLINE = 1.2
LW_THIN = 0.7
LW_DIMENSION = 0.7
LW_CENTER = 0.8

# --- Dash patterns ---
DASH_CENTERLINE = "10,4,2,4"
DASH_HIDDEN = "5,3"

# --- Convenience style dicts ---
STYLE_OUTLINE_THICK = {"stroke": COL_CAD_OUTLINE, "stroke_width": LW_OUTLINE_THICK}
STYLE_OUTLINE = {"stroke": COL_CAD_OBJECT, "stroke_width": LW_OUTLINE}
STYLE_DIMENSION = {"stroke": COL_CAD_DIM, "stroke_width": LW_DIMENSION}
STYLE_CENTERLINE = {"stroke": COL_CAD_CENTER, "stroke_width": LW_CENTER}


# --- Status watermark palette ---
# The three lifecycle states of the Engineering-Drawings-as-Code standard.
STATUS_WATERMARKS = {
    "DRAFT": {
        "text": "DRAFT",
        "color": "#ffaa00",
    },
    "CONCEPT": {
        "text": "CONCEPT — NOT FOR CONSTRUCTION",
        "color": "#ff4444",
    },
    "ISSUED": {
        "text": "ISSUED FOR CONSTRUCTION",
        "color": "#00cc66",
    },
}

# Statuses accepted in meta.yaml, in lifecycle order.
STATUS_ORDER = ["DRAFT", "CONCEPT", "ISSUED"]
