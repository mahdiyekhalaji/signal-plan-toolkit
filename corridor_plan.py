"""
Corridor strip plan generator.

Places each signalized intersection at its true chainage along a stationed
baseline and draws the link segments between them, using the same symbol
library and layer scheme as the single-intersection generator.

    python corridor_plan.py data/yonge_corridor_raw.csv corridor/yonge.json \
        -o output/yonge-corridor.dxf

Each intersection becomes a block, so editing one in AutoCAD updates every
instance of that geometry. Chainage comes from the count coordinates, which
places the signals to within a few metres of their real spacing.
"""
from __future__ import annotations

import argparse
from types import SimpleNamespace

import corridor
import intersection_plan as ip
from ezdxf.enums import TextEntityAlignment

STATION_INTERVAL = 100.0     # m between station ticks
MATCH_INTERVAL = 500.0       # m between match lines


def draw_corridor(doc, signals, cfg):
    msp = doc.modelspace()
    total = signals[-1].chainage

    # each intersection into its own block, legs sized to half the gap
    for i, s in enumerate(signals):
        up = s.chainage - signals[i - 1].chainage if i else 200.0
        dn = signals[i + 1].chainage - s.chainage if i < len(signals) - 1 else 200.0
        leg = max(35.0, min(up, dn) / 2.0 - 5.0)

        icfg = SimpleNamespace(
            major_lanes=s.cross_lanes, minor_lanes=s.through_lanes,
            lane_width=cfg.lane_width, radius=s.radius,
            crosswalk_width=3.0, sidewalk_offset=2.5, sidewalk_width=2.0,
            leg=leg, major_name="", minor_name="", transit_stop=False,
            median_ew=s.median_ew, median_ns=s.median_ns,
            approaches={
                "n": ip.default_lanes(s.through_lanes),
                "s": ip.default_lanes(s.through_lanes),
                "e": ip.default_lanes(s.cross_lanes),
                "w": ip.default_lanes(s.cross_lanes),
            },
        )
        blk = doc.blocks.new(f"INT-{s.px or s.count_id}")
        inter = ip.Intersection(doc, icfg)
        inter.msp = blk
        inter.curbs()
        inter.markings()
        inter.equipment()
        msp.add_blockref(blk.name, (0, -s.chainage), dxfattribs={"layer": "C-ROAD"})
        for w in ip.lane_balance(inter.approaches) + ip.through_alignment(inter.approaches):
            print(f"  {s.name[:30]}: {w}")

    # arterial edges between intersections, matching the intersection cross-section
    med = (signals[0].median_ns or 0.0) / 2.0
    hw = med + cfg.through_lanes * cfg.lane_width
    for i in range(len(signals) - 1):
        a, b = signals[i], signals[i + 1]
        gap_top = -a.chainage - max(35.0, min(
            a.chainage - signals[i - 1].chainage if i else 200.0,
            b.chainage - a.chainage) / 2.0 - 5.0)
        gap_bot = -b.chainage + max(35.0, min(
            b.chainage - a.chainage,
            signals[i + 2].chainage - b.chainage if i + 2 < len(signals) else 200.0) / 2.0 - 5.0)
        for x in (-hw, hw):
            msp.add_lwpolyline([(x, gap_top), (x, gap_bot)], dxfattribs={"layer": "C-CURB"})
        for x in (-hw - 2.5, -hw - 4.5, hw + 2.5, hw + 4.5):
            msp.add_lwpolyline([(x, gap_top), (x, gap_bot)], dxfattribs={"layer": "C-PED"})
        if med:
            for x in (-med, med):
                msp.add_lwpolyline([(x, gap_top), (x, gap_bot)],
                                   dxfattribs={"layer": "C-ISLD"})
        else:
            msp.add_lwpolyline([(0, gap_top), (0, gap_bot)],
                               dxfattribs={"layer": "C-MARK-YELW"})
        for k in range(1, cfg.through_lanes):
            for x in (med + k * cfg.lane_width, -(med + k * cfg.lane_width)):
                msp.add_lwpolyline([(x, gap_top), (x, gap_bot)],
                                   dxfattribs={"layer": "C-MARK-LANE"})

    # stationed baseline to the left of the corridor
    bx = -hw - 22.0
    msp.add_lwpolyline([(bx, 20), (bx, -total - 20)], dxfattribs={"layer": "C-ANNO"})
    st = 0.0
    while st <= total:
        msp.add_lwpolyline([(bx - 2, -st), (bx + 2, -st)], dxfattribs={"layer": "C-ANNO"})
        msp.add_text(f"{int(st // 1000)}+{st % 1000:06.2f}",
                     dxfattribs={"layer": "C-ANNO", "height": ip.TXT_S, "style": "ANNO"}
                     ).set_placement((bx - 4, -st), align=TextEntityAlignment.MIDDLE_RIGHT)
        st += STATION_INTERVAL

    # match lines
    ml = MATCH_INTERVAL
    while ml < total:
        msp.add_lwpolyline([(bx - 6, -ml), (hw + 30, -ml)], dxfattribs={"layer": "C-DIMS"})
        msp.add_text(f"MATCH LINE STA {int(ml // 1000)}+{ml % 1000:06.2f}",
                     dxfattribs={"layer": "C-DIMS", "height": ip.TXT_M, "style": "ANNO"}
                     ).set_placement((hw + 32, -ml), align=TextEntityAlignment.MIDDLE_LEFT)
        ml += MATCH_INTERVAL

    # signal labels with timing results
    for s in signals:
        txt = (f"{s.name}   PX {s.px}   STA {int(s.chainage // 1000)}+"
               f"{s.chainage % 1000:06.2f}")
        msp.add_text(txt, dxfattribs={"layer": "C-ANNO", "height": ip.TXT_M,
                                      "style": "ANNO"}
                     ).set_placement((hw + 12, -s.chainage + 4),
                                     align=TextEntityAlignment.MIDDLE_LEFT)
        if s.cycle:
            txt2 = (f"cycle {s.cycle:.0f} s   arterial green {s.green_art:.0f} s   "
                    f"offset {s.offset:.0f} s")
            msp.add_text(txt2, dxfattribs={"layer": "C-ANNO", "height": ip.TXT_S,
                                           "style": "ANNO"}
                         ).set_placement((hw + 12, -s.chainage),
                                         align=TextEntityAlignment.MIDDLE_LEFT)

    msp.add_blockref("NORTH-ARROW", (bx - 30, -40), dxfattribs={"layer": "C-LEGEND"})
    msp.add_blockref("SCALE-BAR-500", (bx - 70, 40), dxfattribs={"layer": "C-LEGEND"})


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("count_file")
    p.add_argument("config")
    p.add_argument("--period", choices=("am", "pm", "all"), default="pm")
    p.add_argument("--speed", type=float, default=50.0)
    p.add_argument("--through-lanes", type=int, default=3)
    p.add_argument("--cross-lanes", type=int, default=2)
    p.add_argument("--lane-width", type=float, default=3.5)
    p.add_argument("--radius", type=float, default=12.0)
    p.add_argument("--no-timing", dest="timing", action="store_false",
                   help="skip the progression run and draw geometry only")
    p.add_argument("-o", "--out", default="output/corridor-plan.dxf")
    args = p.parse_args()

    signals = corridor.build_signals(args.count_file, args.config, args.period)
    if args.timing:
        cycle = corridor.webster(signals)
        corridor.splits(signals)
        corridor.optimize_offsets(signals, cycle, args.speed / 3.6)

    doc = ip.new_doc()
    ip.build_blocks(doc)
    draw_corridor(doc, signals, args)
    ip.build_sheet(doc, SimpleNamespace(major_name="YONGE ST CORRIDOR",
                                        minor_name="STEELES TO FINCH"))
    doc.saveas(args.out)
    print(f"wrote {args.out}  ({len(signals)} intersections, "
          f"{signals[-1].chainage:.0f} m)")


if __name__ == "__main__":
    main()
