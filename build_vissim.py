"""
Build and run the Yonge and Steeles model in PTV Vissim through the COM API.

Run this on the machine where Vissim is licensed:

    python build_vissim.py --alt A
    python build_vissim.py --alt A --runs 10 --export

It creates the network from scratch (links, connectors, vehicle inputs, routes,
signal controller, node, evaluation settings), runs the simulation, and exports
node results ready for `vissim.py compare`.

Geometry is built from coordinates rather than traced from a map, so the
intersection is a clean orthogonal layout at the right dimensions rather than
the true skew of Steeles against Yonge. Lane counts, volumes and timings are the
real ones.

Requires: pywin32 (pip install pywin32) and a working Vissim installation.
COM attribute names shift between Vissim versions; if a line fails, the error
message names the attribute and it is usually a small rename.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

LANE_W = 3.5
APPROACH_LEN = 250.0        # m, long enough to hold the peak queue
DEPART_LEN = 150.0
SETBACK = 15.0              # m from the intersection centre to the link end
HALF_NS = 3 * LANE_W        # Yonge, 3 lanes each way
HALF_EW = 4 * LANE_W        # Steeles, 4 lanes on the widest approach

# movements: (approach, turn) -> volume, from the PM peak hour count
VOLUMES = {
    ("N", "L"): 134, ("N", "T"): 944, ("N", "R"): 140,
    ("S", "L"): 151, ("S", "T"): 976, ("S", "R"): 52,
    ("E", "L"): 183, ("E", "T"): 771, ("E", "R"): 198,
    ("W", "L"): 254, ("W", "T"): 771, ("W", "R"): 335,
}

# lane count per approach, and which lane index carries which movement.
# lane 1 is the leftmost (next to the centreline).
APPROACH_LANES = {"N": 4, "S": 4, "E": 4, "W": 4}
MOVEMENT_LANES = {"L": [1], "T": [2, 3], "R": [4]}
DEPART_LANES = 3

# heading of each approach, in degrees, measured from east, counterclockwise
# N approach travels north (+y), S travels south, E travels east, W west
HEADINGS = {"N": 90, "S": 270, "E": 0, "W": 180}



def safe_set(obj, attr, value, what=""):
    """Set a COM attribute, reporting rather than crashing when the name differs.

    Vissim renames attributes between versions. A failure here is usually
    cosmetic or fixable by hand, so the build carries on and prints what could
    not be set.
    """
    try:
        obj.SetAttValue(attr, value)
        return True
    except Exception as exc:
        msg = str(exc).split(",")[-1].strip(" )'")
        print(f"    note: could not set {attr}"
              f"{' for ' + what if what else ''} ({msg})")
        return False



def wkt_line(points):
    """Vissim 2026 takes geometry as a WKT LINESTRING rather than a point list."""
    body = ", ".join(f"{x} {y} {z}" for x, y, z in points)
    return f"LINESTRING({body})"


def wkt_polygon(points):
    pts = list(points)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    body = ", ".join(f"{x} {y} {z}" for x, y, z in pts)
    return f"POLYGON(({body}))"


def add_link(net, points, lanes, name=""):
    """Add a link, trying the WKT signature first and the point list second."""
    try:
        lk = net.Links.AddLink(0, wkt_line(points), lanes)
    except Exception:
        lk = net.Links.AddLink(0, points, lanes)
    if name:
        safe_set(lk, "Name", name)
    return lk


def add_connector(net, from_lane, from_pos, to_lane, to_pos, lanes, points, name=""):
    """Add a connector, trying the signatures used across Vissim versions."""
    attempts = [
        lambda: net.Links.AddConnector(0, from_lane, from_pos, to_lane, to_pos, lanes),
        lambda: net.Links.AddConnector(0, from_lane, from_pos, to_lane, to_pos,
                                       lanes, wkt_line(points)),
        lambda: net.Links.AddConnector(0, wkt_line(points), from_lane, from_pos,
                                       to_lane, to_pos, lanes),
    ]
    last = None
    for call in attempts:
        try:
            c = call()
            if name:
                safe_set(c, "Name", name)
            return c
        except Exception as exc:
            last = exc
    print(f"    note: could not add connector {name} ({last})")
    return None


def rot(dx, dy, deg):
    import math
    a = math.radians(deg)
    return dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)


def offsets(appr: str):
    """Return (start, end) centreline points for the approach link, metres.

    Each approach sits on the right-hand half of its street, so the link's
    centre is offset sideways from the intersection centreline.
    """
    half = HALF_NS if appr in "NS" else HALF_EW
    lanes = APPROACH_LANES[appr]
    lateral = lanes * LANE_W / 2.0          # centre of the approach lanes
    setback = SETBACK + (HALF_EW if appr in "NS" else HALF_NS)

    if appr == "N":       # travels +y, sits at +x
        return (lateral, -(setback + APPROACH_LEN)), (lateral, -setback)
    if appr == "S":       # travels -y, sits at -x
        return (-lateral, setback + APPROACH_LEN), (-lateral, setback)
    if appr == "E":       # travels +x, sits at -y
        return (-(setback + APPROACH_LEN), -lateral), (-setback, -lateral)
    return ((setback + APPROACH_LEN), lateral), (setback, lateral)   # W


def depart_offsets(appr: str):
    """Departure link for traffic leaving in the given direction."""
    half = HALF_NS if appr in "NS" else HALF_EW
    lateral = DEPART_LANES * LANE_W / 2.0
    setback = SETBACK + (HALF_EW if appr in "NS" else HALF_NS)
    if appr == "N":       # leaving to the north, on the +x half
        return (lateral, setback), (lateral, setback + DEPART_LEN)
    if appr == "S":
        return (-lateral, -setback), (-lateral, -(setback + DEPART_LEN))
    if appr == "E":
        return (setback, -lateral), (setback + DEPART_LEN, -lateral)
    return (-setback, lateral), (-(setback + DEPART_LEN), lateral)


# destination of each movement: which departure link it feeds
DEST = {
    ("N", "L"): "W", ("N", "T"): "N", ("N", "R"): "E",
    ("S", "L"): "E", ("S", "T"): "S", ("S", "R"): "W",
    ("E", "L"): "N", ("E", "T"): "E", ("E", "R"): "S",
    ("W", "L"): "S", ("W", "T"): "W", ("W", "R"): "N",
}


def read_controller(path: str):
    """Read one controller_X.csv into a list of phases."""
    phases = []
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    cycle = float(rows[1][1])
    for r in rows[3:]:
        if not r or r[0] == "total":
            break
        phases.append({"name": r[0], "movements": r[1].split(),
                       "split": float(r[2]), "green": float(r[3]),
                       "yellow": float(r[4]), "all_red": float(r[5])})
    return cycle, phases


SIGNAL_GROUPS = [
    ("NS left", [("N", "L"), ("S", "L")]),
    ("NS through", [("N", "T"), ("N", "R"), ("S", "T"), ("S", "R")]),
    ("EW left", [("E", "L"), ("W", "L")]),
    ("EW through", [("E", "T"), ("E", "R"), ("W", "T"), ("W", "R")]),
]


def build(vis, alt: str, controller_dir: str):
    net = vis.Net

    print("clearing any existing network")
    try:
        vis.New()
    except Exception:
        try:
            vis.LoadNet("", False)
        except Exception as exc:
            print(f"  note: could not clear the network ({exc}); "
                  f"start Vissim with an empty network and rerun")

    # ---------------------------------------------------------------- links
    links = {}
    for appr in "NSEW":
        (x0, y0), (x1, y1) = offsets(appr)
        pts = [(x0, y0, 0.0), (x1, y1, 0.0)]
        lk = add_link(net, pts, APPROACH_LANES[appr], f"{appr}B approach")
        for lane in lk.Lanes:
            safe_set(lane, "Width", LANE_W)
        links[("appr", appr)] = lk
        print(f"  approach {appr}: link {lk.AttValue('No')}")

        (x0, y0), (x1, y1) = depart_offsets(appr)
        lk = add_link(net, [(x0, y0, 0.0), (x1, y1, 0.0)], DEPART_LANES,
                      f"{appr}B departure")
        for lane in lk.Lanes:
            safe_set(lane, "Width", LANE_W)
        links[("dep", appr)] = lk
        print(f"  departure {appr}: link {lk.AttValue('No')}")

    # ----------------------------------------------------------- connectors
    conns = {}
    for (appr, turn), dest in DEST.items():
        src = links[("appr", appr)]
        dst = links[("dep", dest)]
        from_lanes = MOVEMENT_LANES[turn]
        # map onto the receiving lanes: left turns take the inside lane of the
        # departure, right turns the curb lane, through movements line up
        if turn == "L":
            to_lanes = [1]
        elif turn == "R":
            to_lanes = [DEPART_LANES]
        else:
            to_lanes = [2, 3][:len(from_lanes)]

        for fl, tl in zip(from_lanes, to_lanes * len(from_lanes)):
            (sx0, sy0), (sx1, sy1) = offsets(appr)
            (dx0, dy0), (dx1, dy1) = depart_offsets(dest)
            geom = [(sx1, sy1, 0.0), (dx0, dy0, 0.0)]
            c = add_connector(net, src.Lanes.ItemByKey(fl),
                              src.AttValue("Length2D"),
                              dst.Lanes.ItemByKey(tl), 0.0, 1, geom,
                              f"{appr}B{turn}")
            if c is not None:
                conns[(appr, turn, fl)] = c
        print(f"  connector {appr}B{turn}")

    # -------------------------------------------------------------- inputs
    for appr in "NSEW":
        total = sum(v for (a, t), v in VOLUMES.items() if a == appr)
        vi = net.VehicleInputs.AddVehicleInput(0, links[("appr", appr)])
        safe_set(vi, "Name", f"{appr}B input")
        safe_set(vi, "Volume(1)", total, "vehicle input")
        print(f"  input {appr}B: {total} veh/h")

    # -------------------------------------------------------------- routes
    for appr in "NSEW":
        src = links[("appr", appr)]
        pos = max(10.0, src.AttValue("Length2D") - 180.0)
        rd = net.VehicleRoutingDecisionsStatic.AddVehicleRoutingDecisionStatic(
            0, src, pos)
        safe_set(rd, "Name", f"{appr}B routing")
        for turn in "LTR":
            dest = DEST[(appr, turn)]
            dst = links[("dep", dest)]
            rt = rd.VehRoutSta.AddVehicleRouteStatic(0, dst, DEPART_LEN * 0.5)
            safe_set(rt, "RelFlow(1)", VOLUMES[(appr, turn)], "route flow")
            safe_set(rt, "Name", f"{appr}B{turn}")
        print(f"  routes for {appr}B")

    # ------------------------------------------------------------ signals
    cycle, phases = read_controller(os.path.join(controller_dir,
                                                 f"controller_{alt}.csv"))
    sc = net.SignalControllers.AddSignalController(1)
    safe_set(sc, "Name", f"PX131 alternative {alt}")
    safe_set(sc, "CycTm", cycle, "cycle time")
    print(f"  controller: cycle {cycle:.0f} s, {len(phases)} phases")

    t = 0.0
    groups = {}
    for i, (gname, movements) in enumerate(SIGNAL_GROUPS, start=1):
        ph = phases[i - 1]
        sg = sc.SGs.AddSignalGroup(i)
        safe_set(sg, "Name", gname)
        ok = safe_set(sg, "GreenStart", round(t, 1), gname)
        ok &= safe_set(sg, "GreenEnd", round(t + ph["green"], 1), gname)
        safe_set(sg, "Amber", ph["yellow"], gname)
        if not ok:
            print(f"    set {gname} by hand: green {t:.1f} to "
                  f"{t + ph['green']:.1f}, amber {ph['yellow']}")
        groups[gname] = sg
        print(f"    {gname}: green {t:.1f} -> {t + ph['green']:.1f}")
        t += ph["split"]

    # signal heads, one per lane of each movement
    for gname, movements in SIGNAL_GROUPS:
        for appr, turn in movements:
            for fl in MOVEMENT_LANES[turn]:
                src = links[("appr", appr)]
                sh = net.SignalHeads.AddSignalHead(
                    0, src.Lanes.ItemByKey(fl), src.AttValue("Length2D") - 1.0)
                safe_set(sh, "Name", f"{appr}B{turn} lane {fl}")
                safe_set(sh, "SC", 1, "signal head controller")
                safe_set(sh, "SG", groups[gname].AttValue("No"), "signal head group")
    print("  signal heads placed")

    # ---------------------------------------------------------------- node
    extent = SETBACK + max(HALF_NS, HALF_EW) + 10
    poly = [(-extent, -extent, 0.0), (extent, -extent, 0.0),
            (extent, extent, 0.0), (-extent, extent, 0.0)]
    try:
        node = net.Nodes.AddNode(1, wkt_polygon(poly))
    except Exception:
        node = net.Nodes.AddNode(1, poly)
    safe_set(node, "Name", "PX131")
    safe_set(node, "UseForEvaluation", True, "node evaluation flag")
    print("  evaluation node created")


def configure_run(vis, runs: int, warmup: float = 900.0, period: float = 4500.0):
    ev = vis.Evaluation
    safe_set(ev, "NodeResCollectData", True, "node results")
    safe_set(ev, "NodeResFromTime", warmup)
    safe_set(ev, "NodeResToTime", period)
    safe_set(ev, "NodeResInterval", period - warmup)

    sim = vis.Simulation
    safe_set(sim, "SimPeriod", period)
    safe_set(sim, "RandSeed", 42)
    safe_set(sim, "NumRuns", runs)
    safe_set(sim, "RandSeedIncr", 1)
    safe_set(sim, "SimRes", 10)
    safe_set(sim, "UseMaxSimSpeed", True)
    print(f"  {runs} runs, {period:.0f} s each, first {warmup:.0f} s discarded")


def export_results(vis, out_path: str):
    """Write node results to a semicolon separated file the compare tool reads."""
    rows = []
    for r in vis.Net.Nodes.ItemByKey(1).TotRes:
        try:
            mvmt = r.AttValue("Movement")
            delay = r.AttValue("VehDelay(Current, Total, All)")
            queue = r.AttValue("QLen(Current, Total, All)")
        except Exception:
            continue
        rows.append((mvmt, delay, queue))

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("* PTV Vissim node results, exported by build_vissim.py\n")
        fh.write("$MOVEMENTEVALUATION:MOVEMENT;VEHDELAY(ALL);QLEN(ALL)\n")
        for m, d, q in rows:
            fh.write(f"{m};{d};{q}\n")
    print(f"  wrote {out_path} with {len(rows)} movements")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--alt", default="A", choices=list("ABCD"))
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--controllers", default="vissim",
                   help="folder holding controller_A.csv and friends")
    p.add_argument("--save", default=None, help="save the .inpx here")
    p.add_argument("--run", action="store_true", help="run the simulation")
    p.add_argument("--export", action="store_true", help="export node results")
    p.add_argument("--visible", action="store_true", help="show the Vissim window")
    args = p.parse_args()

    try:
        import win32com.client
    except ImportError:
        sys.exit("pywin32 is required: pip install pywin32")

    print("starting Vissim")
    vis = win32com.client.Dispatch("Vissim.Vissim")
    if args.visible:
        # newer versions dropped the Visible attribute; the window shows anyway
        safe_set(vis, "Visible", True, "the Vissim window")

    build(vis, args.alt, args.controllers)
    configure_run(vis, args.runs)

    save_to = args.save or os.path.join(args.controllers,
                                        f"steeles_yonge_{args.alt}.inpx")
    vis.SaveNetAs(os.path.abspath(save_to))
    print(f"saved {save_to}")

    if args.run:
        print("running, this takes a few minutes")
        vis.Simulation.RunContinuous()
        print("done")
        if args.export:
            export_results(vis, os.path.join(
                args.controllers, f"node_results_{args.alt}.att"))

    print("\nnext:")
    print(f"  python vissim.py compare vissim/node_results_{args.alt}.att "
          f"output/timing-alternatives.json --alt {args.alt}")


if __name__ == "__main__":
    main()
