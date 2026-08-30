"""
Parametric signalized intersection plan generator.

Writes a DXF (metres, plotted 1:500 on A1) containing layers, linetypes,
symbol blocks, a drawn intersection and a title-block sheet.

    python intersection_plan.py --major-lanes 3 --minor-lanes 2 --radius 12

Requires: ezdxf
"""
from __future__ import annotations

import argparse
import ezdxf
from ezdxf.enums import TextEntityAlignment

TXT_S, TXT_M, TXT_L = 1.25, 1.75, 2.50   # 2.5 / 3.5 / 5.0 mm on paper at 1:500

LAYERS = [
    ("V-BASE", 8, "CONTINUOUS", "Aerial photo / GIS reference"),
    ("C-PROP", 9, "PROPERTY", "Property / ROW line"),
    ("C-ROAD", 7, "CONTINUOUS", "Edge of pavement"),
    ("C-CURB", 2, "CONTINUOUS", "Curb and gutter"),
    ("C-ISLD", 5, "CONTINUOUS", "Raised islands and medians"),
    ("C-PED", 3, "CONTINUOUS", "Sidewalk, curb ramps, TWSI"),
    ("C-MARK", 7, "CONTINUOUS", "White pavement markings"),
    ("C-MARK-LANE", 7, "LANE_LINE", "Broken lane line"),
    ("C-MARK-YELW", 2, "CONTINUOUS", "Yellow pavement markings"),
    ("C-MARK-STOP", 7, "CONTINUOUS", "Stop bars"),
    ("C-MARK-EXT", 7, "EXT_LINE", "Extension lines through intersection"),
    ("C-SGNL", 1, "CONTINUOUS", "Signal heads, poles, pushbuttons"),
    ("C-SGNL-UG", 30, "UG_CONDUIT", "Underground conduit and junction boxes"),
    ("C-SGNL-DET", 40, "CONTINUOUS", "Detection"),
    ("C-SIGN", 30, "CONTINUOUS", "Regulatory and warning signs"),
    ("C-TRAN", 6, "CONTINUOUS", "Transit stops and platforms"),
    ("C-BLDG", 8, "CONTINUOUS", "Buildings and driveways"),
    ("C-DIMS", 4, "CONTINUOUS", "Dimensions and radii"),
    ("C-ANNO", 7, "CONTINUOUS", "Notes, labels, leaders"),
    ("C-LEGEND", 7, "CONTINUOUS", "Legend, north arrow, scale bar"),
    ("PALETTE", 253, "CONTINUOUS", "Symbol palette - delete when done"),
]

LINETYPES = [
    ("LANE_LINE", [9.0, 3.0, -6.0], "Lane line  3 m dash / 6 m gap"),
    ("EXT_LINE", [2.0, 1.0, -1.0], "Extension line  1 m / 1 m"),
    ("CHANNEL_LINE", [1.0, 0.5, -0.5], "Channelizing  0.5 m / 0.5 m"),
    ("UG_CONDUIT", [3.0, 2.0, -0.5, 0.0, -0.5], "Underground conduit"),
    ("PROPERTY", [6.0, 4.0, -0.75, 0.5, -0.75], "Property line"),
]



# plotted lineweights, 1/100 mm. Curbs read heaviest because they define the
# design; annotation and extension lines sit lightest.
LINEWEIGHTS = {
    "C-CURB": 50, "C-ROAD": 50, "C-ISLD": 40, "C-PROP": 35,
    "C-MARK": 35, "C-MARK-STOP": 40, "C-MARK-YELW": 35,
    "C-MARK-LANE": 25, "C-MARK-EXT": 18,
    "C-PED": 25, "C-SGNL": 35, "C-SGNL-UG": 18, "C-SGNL-DET": 25,
    "C-SIGN": 25, "C-TRAN": 25, "C-BLDG": 18,
    "C-DIMS": 18, "C-ANNO": 18, "C-LEGEND": 25, "V-BASE": 13, "PALETTE": 13,
}


# --------------------------------------------------------------------- setup
def new_doc():
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 6
    doc.header["$MEASUREMENT"] = 1
    for name, pattern, desc in LINETYPES:
        doc.linetypes.add(name, pattern=pattern, description=desc)
    for name, color, lt, desc in LAYERS:
        lay = doc.layers.add(name, color=color, linetype=lt)
        lay.description = desc
        lay.dxf.lineweight = LINEWEIGHTS.get(name, 25)
    doc.styles.add("ANNO", font="calibri.ttf")
    dim = doc.dimstyles.duplicate_entry("EZDXF", "1-500")
    dim.dxf.dimtxt, dim.dxf.dimasz = TXT_S, 0.75
    dim.dxf.dimexe, dim.dxf.dimexo, dim.dxf.dimgap = 0.35, 0.60, 0.35
    dim.dxf.dimdec, dim.dxf.dimlunit, dim.dxf.dimclrt = 2, 2, 4
    dim.dxf.dimtxsty = "ANNO"
    return doc



# ------------------------------------------------------------- arrow blocks
# Arrows point +y, the direction of travel. Left branches to -x, right to +x,
# which is the mirror people get wrong. One block per movement combination, so
# a shared lane carries a single combined arrow rather than two separate ones.
MOVEMENT_BLOCKS = {
    "l": "ARROW-L", "t": "ARROW-T", "r": "ARROW-R",
    "lt": "ARROW-LT", "tl": "ARROW-LT",
    "tr": "ARROW-TR", "rt": "ARROW-TR",
    "lr": "ARROW-LR", "rl": "ARROW-LR",
    "ltr": "ARROW-LTR", "lrt": "ARROW-LTR", "tlr": "ARROW-LTR",
}


def build_arrows(doc):
    a = {"layer": "0"}

    def make(name, mv):
        b = doc.blocks.new(name)
        top = 4.2 if "t" in mv else 3.9
        b.add_lwpolyline([(-0.25, 0), (0.25, 0), (0.25, top - 0.7), (-0.25, top - 0.7)],
                         close=True, dxfattribs=a)
        if "t" in mv:
            b.add_lwpolyline([(-0.75, 3.2), (0.75, 3.2), (0, 4.2)],
                             close=True, dxfattribs=a)
        if "l" in mv:
            b.add_lwpolyline([(-0.25, 2.6), (-1.4, 2.6), (-1.4, 3.2), (-0.25, 3.2)],
                             close=True, dxfattribs=a)
            b.add_lwpolyline([(-1.4, 2.15), (-2.4, 2.9), (-1.4, 3.65)],
                             close=True, dxfattribs=a)
        if "r" in mv:
            b.add_lwpolyline([(0.25, 2.6), (1.4, 2.6), (1.4, 3.2), (0.25, 3.2)],
                             close=True, dxfattribs=a)
            b.add_lwpolyline([(1.4, 2.15), (2.4, 2.9), (1.4, 3.65)],
                             close=True, dxfattribs=a)

    for mv, name in (("t", "ARROW-T"), ("l", "ARROW-L"), ("r", "ARROW-R"),
                     ("lt", "ARROW-LT"), ("tr", "ARROW-TR"), ("lr", "ARROW-LR"),
                     ("ltr", "ARROW-LTR")):
        make(name, mv)


def arrow_block(movements: str) -> str:
    key = "".join(sorted(set(movements), key="ltr".index))
    return MOVEMENT_BLOCKS.get(key, "ARROW-T")


def default_lanes(n: int) -> list[str]:
    """Lane movements from the centreline outward, for n lanes per direction."""
    if n <= 1:
        return ["ltr"]
    if n == 2:
        return ["lt", "tr"]
    return ["l"] + ["t"] * (n - 2) + ["tr"]


def through_alignment(approaches: dict) -> list:
    """Opposing approaches should offer the same number of through lanes.

    If they do not, a through vehicle has to shift laterally while crossing,
    which is the trajectory problem that shows up as weaving in simulation.
    """
    warn = []
    for a, b in (("n", "s"), ("e", "w")):
        ta = sum(1 for m in approaches.get(a, []) if "t" in m)
        tb = sum(1 for m in approaches.get(b, []) if "t" in m)
        if ta != tb:
            warn.append(f"{a.upper()} approach has {ta} through lanes but the "
                        f"{b.upper()} approach has {tb}: through traffic must "
                        f"shift lanes inside the intersection")
    return warn


def lane_balance(approaches: dict) -> list:
    """Receiving lane check. A turn must land in a lane it can stay in.

    Left from the north approach enters the west leg, right enters the east
    leg, and so on. If more lanes turn than the receiving street has, drivers
    must change lanes inside the intersection.
    """
    left_to = {"n": "w", "s": "e", "e": "n", "w": "s"}
    right_to = {"n": "e", "s": "w", "e": "s", "w": "n"}
    warn = []
    for appr, lanes in approaches.items():
        nl = sum(1 for m in lanes if "l" in m)
        nr = sum(1 for m in lanes if "r" in m)
        nt = sum(1 for m in lanes if "t" in m)
        for count, dest, kind in ((nl, left_to[appr], "left"),
                                  (nr, right_to[appr], "right"),
                                  (nt, appr, "through")):
            recv = len(approaches.get(dest, []))
            if count > recv:
                warn.append(f"{appr.upper()} approach: {count} {kind} lanes into "
                            f"{recv} receiving lanes on the {dest.upper()} leg")
    return warn


def build_blocks(doc):
    a = {"layer": "0"}

    b = doc.blocks.new("SIG-3SEC")
    b.add_lwpolyline([(-0.5, 0), (0.5, 0), (0.5, 3.0), (-0.5, 3.0)], close=True, dxfattribs=a)
    for y in (0.5, 1.5, 2.5):
        b.add_circle((0, y), 0.35, dxfattribs=a)

    b = doc.blocks.new("SIG-PED")
    b.add_lwpolyline([(-0.6, 0), (0.6, 0), (0.6, 1.6), (-0.6, 1.6)], close=True, dxfattribs=a)
    b.add_text("P", dxfattribs={**a, "height": 0.9, "style": "ANNO"}
               ).set_placement((0, 0.8), align=TextEntityAlignment.MIDDLE_CENTER)

    b = doc.blocks.new("SIG-PB")
    b.add_circle((0, 0), 0.55, dxfattribs=a)
    b.add_text("PB", dxfattribs={**a, "height": 0.55, "style": "ANNO"}
               ).set_placement((0, 0), align=TextEntityAlignment.MIDDLE_CENTER)

    b = doc.blocks.new("SIG-POLE")
    b.add_circle((0, 0), 0.6, dxfattribs=a)
    b.add_line((-0.42, -0.42), (0.42, 0.42), dxfattribs=a)
    b.add_line((-0.42, 0.42), (0.42, -0.42), dxfattribs=a)

    b = doc.blocks.new("SIG-MASTARM")
    b.add_circle((0, 0), 0.8, dxfattribs=a)
    b.add_line((-0.57, -0.57), (0.57, 0.57), dxfattribs=a)
    b.add_line((-0.57, 0.57), (0.57, -0.57), dxfattribs=a)
    b.add_lwpolyline([(0.8, 0.25), (8.0, 0.25), (8.0, -0.25), (0.8, -0.25)],
                     close=True, dxfattribs=a)

    b = doc.blocks.new("SIG-CABINET")
    b.add_lwpolyline([(-0.9, -0.6), (0.9, -0.6), (0.9, 0.6), (-0.9, 0.6)],
                     close=True, dxfattribs=a)
    b.add_text("CTRL", dxfattribs={**a, "height": 0.5, "style": "ANNO"}
               ).set_placement((0, 0), align=TextEntityAlignment.MIDDLE_CENTER)

    b = doc.blocks.new("SIG-JB")
    b.add_lwpolyline([(-0.45, -0.35), (0.45, -0.35), (0.45, 0.35), (-0.45, 0.35)],
                     close=True, dxfattribs=a)
    b.add_line((-0.45, -0.35), (0.45, 0.35), dxfattribs=a)

    b = doc.blocks.new("SIGN-POST")
    b.add_circle((0, 0), 0.25, dxfattribs=a)
    b.add_lwpolyline([(-0.8, 0.6), (0.8, 0.6), (0.8, 2.0), (-0.8, 2.0)],
                     close=True, dxfattribs=a)
    b.add_line((0, 0.25), (0, 0.6), dxfattribs=a)
    b.add_attdef("CODE", (0, 1.3), dxfattribs={**a, "height": 0.9, "style": "ANNO",
                                               "prompt": "OTM sign code"}
                 ).set_placement((0, 1.3), align=TextEntityAlignment.MIDDLE_CENTER)

    b = doc.blocks.new("DET-LOOP")
    b.add_lwpolyline([(-1, -1), (1, -1), (1, 1), (-1, 1)], close=True, dxfattribs=a)
    b.add_line((-1, -1), (1, 1), dxfattribs=a)
    b.add_line((-1, 1), (1, -1), dxfattribs=a)

    b = doc.blocks.new("CURB-RAMP-TWSI")
    b.add_lwpolyline([(0, 0), (1.65, 0), (1.65, 0.61), (0, 0.61)], close=True, dxfattribs=a)
    x = 0.10
    while x < 1.6:
        y = 0.11
        while y < 0.60:
            b.add_circle((x, y), 0.045, dxfattribs=a)
            y += 0.19
        x += 0.19

    b = doc.blocks.new("TRAN-STOP")
    b.add_circle((0, 0), 0.3, dxfattribs=a)
    b.add_lwpolyline([(0, 0.3), (0, 2.2), (1.4, 2.2), (1.4, 1.3), (0, 1.3)], dxfattribs=a)

    build_arrows(doc)

    b = doc.blocks.new("NORTH-ARROW")
    b.add_lwpolyline([(0, 0), (-2.2, -3.0), (0, 9.0), (2.2, -3.0)], close=True, dxfattribs=a)
    b.add_text("N", dxfattribs={**a, "height": 2.5, "style": "ANNO"}
               ).set_placement((0, 10.0), align=TextEntityAlignment.BOTTOM_CENTER)

    b = doc.blocks.new("SCALE-BAR-500")
    h = 1.2
    for i in range(5):
        x0 = i * 10.0
        pts = [(x0, 0), (x0 + 10, 0), (x0 + 10, h), (x0, h)]
        b.add_lwpolyline(pts, close=True, dxfattribs=a)
        if i % 2 == 0:
            hp = b.add_hatch(color=7, dxfattribs=a)
            hp.paths.add_polyline_path(pts, is_closed=True)
        b.add_text(f"{i * 10}", dxfattribs={**a, "height": TXT_S, "style": "ANNO"}
                   ).set_placement((x0, -0.6), align=TextEntityAlignment.TOP_CENTER)
    b.add_text("50", dxfattribs={**a, "height": TXT_S, "style": "ANNO"}
               ).set_placement((50, -0.6), align=TextEntityAlignment.TOP_CENTER)
    b.add_text("METRES   1:500", dxfattribs={**a, "height": TXT_S, "style": "ANNO"}
               ).set_placement((25, -2.4), align=TextEntityAlignment.TOP_CENTER)


# ------------------------------------------------------------------ drawing
class Intersection:
    def __init__(self, doc, cfg):
        self.doc, self.msp, self.cfg = doc, doc.modelspace(), cfg
        # a raised median occupies the centre, so lanes are measured from its
        # edge rather than from the centreline
        self.med_ew = (getattr(cfg, "median_ew", 0.0) or 0.0) / 2.0
        self.med_ns = (getattr(cfg, "median_ns", 0.0) or 0.0) / 2.0
        self.hw_ew = self.med_ew + cfg.major_lanes * cfg.lane_width   # half width, major
        self.hw_ns = self.med_ns + cfg.minor_lanes * cfg.lane_width   # half width, minor
        given = getattr(cfg, "approaches", None) or {}
        self.approaches = {
            "e": given.get("e") or default_lanes(cfg.major_lanes),
            "w": given.get("w") or default_lanes(cfg.major_lanes),
            "n": given.get("n") or default_lanes(cfg.minor_lanes),
            "s": given.get("s") or default_lanes(cfg.minor_lanes),
        }

    # small helpers
    def pl(self, pts, layer, closed=False):
        self.msp.add_lwpolyline(pts, close=closed, dxfattribs={"layer": layer})

    def arc(self, c, r, a0, a1, layer):
        self.msp.add_arc(c, r, a0, a1, dxfattribs={"layer": layer})

    def blk(self, name, x, y, rot=0, layer="C-SGNL"):
        return self.msp.add_blockref(name, (x, y),
                                     dxfattribs={"layer": layer, "rotation": rot})

    def bar(self, x0, y0, x1, y1, layer="C-MARK"):
        self.pl([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer, closed=True)

    def note(self, x, y, txt, h=TXT_S, rot=0, layer="C-ANNO"):
        self.msp.add_text(txt, dxfattribs={"layer": layer, "height": h,
                                           "style": "ANNO", "rotation": rot}
                          ).set_placement((x, y), align=TextEntityAlignment.MIDDLE_LEFT)

    # curbs, corner returns, sidewalks
    def corner_radius(self, sx, sy) -> float:
        name = {(-1, 1): "nw", (1, 1): "ne", (1, -1): "se", (-1, -1): "sw"}[(sx, sy)]
        return getattr(self.cfg, f"radius_{name}", None) or self.cfg.radius

    def curbs(self):
        c, leg = self.cfg, self.cfg.leg
        for sx, sy in ((-1, 1), (1, 1), (1, -1), (-1, -1)):
            r = self.corner_radius(sx, sy)
            cx, cy = sx * (self.hw_ns + r), sy * (self.hw_ew + r)
            a0, a1 = {(-1, 1): (270, 360), (1, 1): (180, 270),
                      (1, -1): (90, 180), (-1, -1): (0, 90)}[(sx, sy)]
            self.pl([(sx * leg, sy * self.hw_ew), (cx, sy * self.hw_ew)], "C-CURB")
            self.pl([(sx * self.hw_ns, sy * leg), (sx * self.hw_ns, cy)], "C-CURB")
            self.arc((cx, cy), r, a0, a1, "C-CURB")
            for off in (c.sidewalk_offset, c.sidewalk_offset + c.sidewalk_width):
                self.pl([(sx * leg, sy * (self.hw_ew + off)),
                         (cx, sy * (self.hw_ew + off))], "C-PED")
                self.pl([(sx * (self.hw_ns + off), sy * leg),
                         (sx * (self.hw_ns + off), cy)], "C-PED")
                self.arc((cx, cy), r - off, a0, a1, "C-PED")

    # lane lines, stop bars, crosswalks, arrows
    def markings(self):
        c, leg, lw = self.cfg, self.cfg.leg, self.cfg.lane_width
        me, mn = self.med_ew, self.med_ns          # half median widths
        xw0, xw1 = self.hw_ns + 1.5, self.hw_ns + 1.5 + c.crosswalk_width   # E-W legs
        yw0, yw1 = self.hw_ew + 1.5, self.hw_ew + 1.5 + c.crosswalk_width   # N-S legs
        sb_x, sb_y = xw1 + 1.0, yw1 + 1.0        # stop bar, 1 m behind crosswalk

        # centre line only where there is no raised median to separate directions
        if not me:
            self.pl([(-leg, 0), (-sb_x - 0.6, 0)], "C-MARK-YELW")
            self.pl([(sb_x + 0.6, 0), (leg, 0)], "C-MARK-YELW")
        if not mn:
            self.pl([(0, -leg), (0, -sb_y - 0.6)], "C-MARK-YELW")
            self.pl([(0, sb_y + 0.6), (0, leg)], "C-MARK-YELW")

        for i in range(1, c.major_lanes):
            for y in (me + i * lw, -(me + i * lw)):
                self.pl([(-leg, y), (-sb_x - 0.6, y)], "C-MARK-LANE")
                self.pl([(sb_x + 0.6, y), (leg, y)], "C-MARK-LANE")
        for i in range(1, c.minor_lanes):
            for x in (mn + i * lw, -(mn + i * lw)):
                self.pl([(x, -leg), (x, -sb_y - 0.6)], "C-MARK-LANE")
                self.pl([(x, sb_y + 0.6), (x, leg)], "C-MARK-LANE")

        # extensions across the intersection, one per lane line, so any lateral
        # shift between an approach lane and its receiving lane is visible
        for i in range(1, c.major_lanes):
            for y in (me + i * lw, -(me + i * lw)):
                self.pl([(-xw0, y), (xw0, y)], "C-MARK-EXT")
        for i in range(1, c.minor_lanes):
            for x in (mn + i * lw, -(mn + i * lw)):
                self.pl([(x, -yw0), (x, yw0)], "C-MARK-EXT")

        for coords in ((-sb_x - 0.6, -self.hw_ew, -sb_x, -me),
                       (sb_x, me, sb_x + 0.6, self.hw_ew),
                       (mn, -sb_y - 0.6, self.hw_ns, -sb_y),
                       (-self.hw_ns, sb_y, -mn, sb_y + 0.6)):
            x0, y0, x1, y1 = coords
            self.bar(x0, y0, x1, y1, "C-MARK-STOP")
            h = self.msp.add_hatch(color=7, dxfattribs={"layer": "C-MARK-STOP"})
            h.paths.add_polyline_path([(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                                      is_closed=True)

        self.crosswalk(-xw1, -xw0, -self.hw_ew, self.hw_ew, True)
        self.crosswalk(xw0, xw1, -self.hw_ew, self.hw_ew, True)
        self.crosswalk(-self.hw_ns, self.hw_ns, -yw1, -yw0, False)
        self.crosswalk(-self.hw_ns, self.hw_ns, yw0, yw1, False)

        # lane use arrows: one set near the stop bar, a second set 25 m back,
        # each showing only the movements that lane is allowed to make
        for setback in (9.0, 25.0):
            for i, mv in enumerate(self.approaches["e"]):
                self.blk(arrow_block(mv), -sb_x - setback, -(me + (i + 0.5) * lw),
                         90, "C-MARK")
            for i, mv in enumerate(self.approaches["w"]):
                self.blk(arrow_block(mv), sb_x + setback, me + (i + 0.5) * lw,
                         270, "C-MARK")
            for i, mv in enumerate(self.approaches["n"]):
                self.blk(arrow_block(mv), mn + (i + 0.5) * lw, -sb_y - setback,
                         0, "C-MARK")
            for i, mv in enumerate(self.approaches["s"]):
                self.blk(arrow_block(mv), -(mn + (i + 0.5) * lw), sb_y + setback,
                         180, "C-MARK")

    def crosswalk(self, x0, x1, y0, y1, along_x):
        self.pl([(x0, y0), (x1, y0)], "C-MARK")
        self.pl([(x0, y1), (x1, y1)], "C-MARK")
        if along_x:
            t = y0 + 0.5
            while t + 0.5 < y1:
                self.bar(x0, t, x1, t + 0.5)
                t += 1.0
        else:
            t = x0 + 0.5
            while t + 0.5 < x1:
                self.bar(t, y0, t + 0.5, y1)
                t += 1.0

    # signals, detection, ramps, transit
    def equipment(self):
        c, lw = self.cfg, self.cfg.lane_width
        px, py = self.hw_ns + 5.0, self.hw_ew + 7.0     # pole positions
        hx, hy = self.hw_ns + 10.0, self.hw_ew + 6.5    # head positions

        self.blk("SIG-MASTARM", -px, py, 0)
        self.blk("SIG-MASTARM", px, py, 270)
        self.blk("SIG-MASTARM", px, -py, 180)
        self.blk("SIG-MASTARM", -px, -py, 90)

        for i in range(min(2, c.minor_lanes)):
            x = self.med_ns + (i + 0.5) * lw
            self.blk("SIG-3SEC", x, hy, 180)        # faces northbound
            self.blk("SIG-3SEC", -x, -hy, 0)        # faces southbound
        for i in range(min(2, c.major_lanes)):
            y = self.med_ew + (i + 0.5) * lw
            self.blk("SIG-3SEC", hx, -y, 90)        # faces eastbound
            self.blk("SIG-3SEC", -hx, y, 270)       # faces westbound

        for sx, sy in ((-1, 1), (1, 1), (1, -1), (-1, -1)):
            self.blk("SIG-PED", sx * (self.hw_ns + 3.0), sy * (self.hw_ew + 6.0),
                     0 if sy > 0 else 180)
            self.blk("SIG-PED", sx * (self.hw_ns + 6.0), sy * (self.hw_ew + 2.0),
                     90 if sx > 0 else 270)
            self.blk("SIG-PB", sx * (self.hw_ns + 4.0), sy * (self.hw_ew + 3.0))
            # ramp centred on the crosswalk it serves, TWSI bar square to the
            # crossing, so the pedestrian path runs straight off the ramp
            cw = self.cfg.crosswalk_width
            xw_mid = self.hw_ns + 1.5 + cw / 2.0        # crosswalk across the E-W street
            yw_mid = self.hw_ew + 1.5 + cw / 2.0        # crosswalk across the N-S street
            self.msp.add_blockref(
                "CURB-RAMP-TWSI", (sx * xw_mid - 0.825, sy * self.hw_ew - sy * 0.71),
                dxfattribs={"layer": "C-PED"})
            self.msp.add_blockref(
                "CURB-RAMP-TWSI", (sx * self.hw_ns - sx * 0.71, sy * yw_mid + 0.825),
                dxfattribs={"layer": "C-PED", "rotation": 270})
            self.blk("SIG-JB", sx * (self.hw_ns + 6.5), sy * (self.hw_ew + 5.5), 0, "C-SGNL-UG")

        jx, jy = self.hw_ns + 6.5, self.hw_ew + 5.5
        self.pl([(-jx, jy), (jx, jy), (jx, -jy), (-jx, -jy), (-jx, jy)], "C-SGNL-UG")
        self.blk("SIG-CABINET", -jx - 3, jy + 4, 0)
        self.pl([(-jx - 3, jy + 4), (-jx, jy)], "C-SGNL-UG")

        det = self.hw_ns + 9.5, self.hw_ew + 9.5
        for i in range(self.cfg.major_lanes):
            y = self.med_ew + (i + 0.5) * lw
            self.msp.add_blockref("DET-LOOP", (-det[0], -y), dxfattribs={"layer": "C-SGNL-DET"})
            self.msp.add_blockref("DET-LOOP", (det[0], y), dxfattribs={"layer": "C-SGNL-DET"})
        for i in range(self.cfg.minor_lanes):
            x = self.med_ns + (i + 0.5) * lw
            self.msp.add_blockref("DET-LOOP", (x, -det[1]), dxfattribs={"layer": "C-SGNL-DET"})
            self.msp.add_blockref("DET-LOOP", (-x, det[1]), dxfattribs={"layer": "C-SGNL-DET"})

        for sx, sy in ((1, 1), (-1, -1)):
            self.msp.add_blockref("SIGN-POST", (sx * (self.hw_ns + 7.0), sy * (self.hw_ew + 1.5)),
                                  dxfattribs={"layer": "C-SIGN"}).add_auto_attribs({"CODE": "Rb-11"})

        if self.cfg.transit_stop:
            self.blk("TRAN-STOP", 30.0, self.hw_ew + 1.5, 0, "C-TRAN")
            self.pl([(24, self.hw_ew), (40, self.hw_ew), (40, self.hw_ew + 2.0),
                     (24, self.hw_ew + 2.0)], "C-TRAN", closed=True)


    def medians(self):
        """Raised median with a tapered nose, set back from the stop bar."""
        c = self.cfg
        sb_x = self.hw_ns + 1.5 + c.crosswalk_width + 1.0
        sb_y = self.hw_ew + 1.5 + c.crosswalk_width + 1.0
        nose, taper, setback = 0.6, 12.0, 3.0

        if self.med_ew:
            h = self.med_ew
            for sgn in (-1, 1):
                x_end = sgn * (sb_x + setback)
                x_tap = sgn * (sb_x + setback + taper)
                x_far = sgn * c.leg
                self.pl([(x_far, h), (x_tap, h), (x_end, nose / 2),
                         (x_end, -nose / 2), (x_tap, -h), (x_far, -h)], "C-ISLD")
        if self.med_ns:
            h = self.med_ns
            for sgn in (-1, 1):
                y_end = sgn * (sb_y + setback)
                y_tap = sgn * (sb_y + setback + taper)
                y_far = sgn * c.leg
                self.pl([(h, y_far), (h, y_tap), (nose / 2, y_end),
                         (-nose / 2, y_end), (-h, y_tap), (-h, y_far)], "C-ISLD")


    def channelized_rights(self):
        """Raised islands separating the right turns, as at Yonge and Steeles.

        The corner return becomes the outer edge of a bypass lane; the island
        sits between that lane and the intersection, with its nose set back
        from the crosswalks.
        """
        corners = getattr(self.cfg, "channelized", "") or ""
        if not corners:
            return
        names = {"nw": (-1, 1), "ne": (1, 1), "se": (1, -1), "sw": (-1, -1)}
        lane = 4.0          # bypass lane width
        for key in [c.strip() for c in corners.split(",") if c.strip()]:
            if key not in names:
                continue
            sx, sy = names[key]
            r = self.corner_radius(sx, sy)
            cx, cy = sx * (self.hw_ns + r), sy * (self.hw_ew + r)
            a0, a1 = {(-1, 1): (270, 360), (1, 1): (180, 270),
                      (1, -1): (90, 180), (-1, -1): (0, 90)}[(sx, sy)]

            # island inner edge, concentric with the corner return
            self.arc((cx, cy), r - lane, a0, a1, "C-ISLD")

            # straight edges running back toward the crosswalks, with the nose
            # held off the through lanes
            ew_edge = sy * (self.hw_ew + lane)
            ns_edge = sx * (self.hw_ns + lane)
            nose_ew = sx * (self.hw_ns + 1.5)
            nose_ns = sy * (self.hw_ew + 1.5)
            self.pl([(cx, ew_edge), (nose_ew, ew_edge)], "C-ISLD")
            self.pl([(ns_edge, cy), (ns_edge, nose_ns)], "C-ISLD")
            self.pl([(nose_ew, ew_edge), (ns_edge, nose_ns)], "C-ISLD")

            # yield line where the bypass rejoins the departure
            import math
            a = math.radians((a0 + a1) / 2.0)
            yx = cx + (r - lane / 2) * math.cos(a)
            yy = cy + (r - lane / 2) * math.sin(a)
            self.msp.add_lwpolyline(
                [(yx - 1.4 * math.sin(a), yy + 1.4 * math.cos(a)),
                 (yx + 1.4 * math.sin(a), yy - 1.4 * math.cos(a))],
                dxfattribs={"layer": "C-MARK"})


    def annotate(self):
        d = {"layer": "C-DIMS"}
        self.msp.add_linear_dim(base=(-30, -self.hw_ew - 15),
                                p1=(-self.cfg.leg + 10, -self.hw_ew),
                                p2=(-self.cfg.leg + 10, 0),
                                text=f"{self.hw_ew:.2f}", dimstyle="1-500",
                                dxfattribs=d).render()
        self.msp.add_linear_dim(base=(-26, -self.hw_ew - 22), p1=(-self.hw_ns, -30),
                                p2=(self.hw_ns, -30), text=f"{2 * self.hw_ns:.2f}",
                                dimstyle="1-500", dxfattribs=d).render()
        for sx, sy in ((-1, 1), (1, 1), (1, -1), (-1, -1)):
            r = self.corner_radius(sx, sy)
            self.note(sx * (self.hw_ns + r + 3), sy * (self.hw_ew + r + 3),
                      f"R{r:.1f}", TXT_S, layer="C-DIMS")
        self.msp.add_linear_dim(
            base=(0, self.hw_ew + 22),
            p1=(-self.hw_ns, self.hw_ew + 1.5),
            p2=(-self.hw_ns, self.hw_ew + 1.5 + self.cfg.crosswalk_width),
            angle=90, text=f"{self.cfg.crosswalk_width:.2f}", dimstyle="1-500",
            dxfattribs={"layer": "C-DIMS"}).render()
        self.note(-self.cfg.leg + 4, 2.5, self.cfg.major_name, TXT_L)
        self.note(2.5, self.cfg.leg - 8, self.cfg.minor_name, TXT_L, rot=90)
        self.note(-42, -38, f"{self.cfg.lane_width} m LANES TYP.   "
                            f"LADDER CROSSWALKS PER OTM BOOK 11")
        self.note(-42, -41, "TWSI 0.61 x 1.65 m AT EACH RAMP PER AODA DOPS")
        self.blk("NORTH-ARROW", -50, 30, 0, "C-LEGEND")
        self.blk("SCALE-BAR-500", -55, -50, 0, "C-LEGEND")

    def palette(self):
        px, py = 160.0, 0.0
        items = ["SIG-3SEC", "SIG-PED", "SIG-PB", "SIG-POLE", "SIG-MASTARM",
                 "SIG-CABINET", "SIG-JB", "SIGN-POST", "DET-LOOP",
                 "CURB-RAMP-TWSI", "TRAN-STOP", "ARROW-T", "ARROW-L",
                 "ARROW-R", "ARROW-LT", "ARROW-TR", "ARROW-LTR"]
        self.msp.add_text("SYMBOL PALETTE", dxfattribs={"layer": "PALETTE",
                                                        "height": TXT_L, "style": "ANNO"}
                          ).set_placement((px, py + 8), align=TextEntityAlignment.BOTTOM_LEFT)
        for i, name in enumerate(items):
            y = py - i * 8.0
            ref = self.msp.add_blockref(name, (px, y), dxfattribs={"layer": "PALETTE"})
            if name == "SIGN-POST":
                ref.add_auto_attribs({"CODE": "Ra-1"})
            self.note(px + 14, y, name, TXT_S, layer="PALETTE")

    def draw(self):
        self.curbs()
        self.markings()
        self.medians()
        self.channelized_rights()
        self.equipment()
        self.annotate()
        self.palette()


# ------------------------------------------------------------------- sheet
def build_sheet(doc, cfg):
    layout = doc.layouts.new("A1 - 1to500")
    layout.page_setup(size=(841, 594), margins=(0, 0, 0, 0), units="mm")
    a = {"layer": "C-ANNO"}
    layout.add_lwpolyline([(10, 10), (831, 10), (831, 584), (10, 584)],
                          close=True, dxfattribs=a)
    x, y, w, h = 641, 10, 190, 90
    layout.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                          close=True, dxfattribs=a)
    for dy in (30, 46, 62, 76):
        layout.add_line((x, y + dy), (x + w, y + dy), dxfattribs=a)
    fields = [
        (80, 3.5, "INTERSECTION IMPROVEMENT PLAN"),
        (68, 3.0, f"LOCATION:  {cfg.major_name} AT {cfg.minor_name}"),
        (54, 3.0, "DRAWING:  EXISTING CONDITIONS / PROPOSED"),
        (38, 3.0, "SCALE 1:500        DATE:            SHEET  1 OF 2"),
        (20, 3.0, "DRAWN BY:"),
        (14, 2.5, "REFERENCES:  TAC GDG, OTM BOOK 11 / 12, AODA DOPS"),
    ]
    for dy, th, txt in fields:
        layout.add_text(txt, dxfattribs={**a, "height": th, "style": "ANNO"}
                        ).set_placement((x + 5, y + dy), align=TextEntityAlignment.MIDDLE_LEFT)
    vp = layout.add_viewport(center=(320, 300), size=(610, 550),
                             view_center_point=(0, 0), view_height=275)
    vp.dxf.status = 1


# -------------------------------------------------------------------- main
def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--major-lanes", type=int, default=3, help="lanes per direction, major street")
    p.add_argument("--minor-lanes", type=int, default=2, help="lanes per direction, minor street")
    p.add_argument("--lane-width", type=float, default=3.5, help="lane width, m")
    p.add_argument("--radius", type=float, default=12.0, help="corner radius, m")
    p.add_argument("--radius-nw", type=float, help="override the NW corner radius")
    p.add_argument("--radius-ne", type=float)
    p.add_argument("--radius-se", type=float)
    p.add_argument("--radius-sw", type=float)
    p.add_argument("--median-ew", type=float, default=0.0,
                   help="raised median width on the E-W street, m (0 = none)")
    p.add_argument("--median-ns", type=float, default=0.0)
    p.add_argument("--channelized", default="",
                   help='corners with a channelized right, e.g. "nw,ne,se,sw"')
    p.add_argument("--crosswalk-width", type=float, default=3.0, help="crosswalk width, m")
    p.add_argument("--sidewalk-offset", type=float, default=2.5, help="curb to sidewalk, m")
    p.add_argument("--sidewalk-width", type=float, default=2.0, help="sidewalk width, m")
    p.add_argument("--leg", type=float, default=70.0, help="length of each leg drawn, m")
    p.add_argument("--major-name", default="MAJOR STREET")
    p.add_argument("--minor-name", default="MINOR STREET")
    p.add_argument("--lanes-n", help='movements per lane, centreline outward, e.g. "l,t,t,tr"')
    p.add_argument("--lanes-s")
    p.add_argument("--lanes-e")
    p.add_argument("--lanes-w")
    p.add_argument("--no-transit-stop", dest="transit_stop", action="store_false")
    p.add_argument("-o", "--out", default="intersection-plan.dxf")
    return p.parse_args()


def main():
    cfg = parse_args()
    cfg.approaches = {a: getattr(cfg, f"lanes_{a}").split(",")
                      for a in ("n", "s", "e", "w") if getattr(cfg, f"lanes_{a}")}
    doc = new_doc()
    build_blocks(doc)
    inter = Intersection(doc, cfg)
    inter.draw()
    build_sheet(doc, cfg)
    doc.saveas(cfg.out)
    print(f"wrote {cfg.out}")
    warns = lane_balance(inter.approaches) + through_alignment(inter.approaches)
    if warns:
        print("\nlane balance warnings:")
        for w in warns:
            print(f"  {w}")


if __name__ == "__main__":
    main()
