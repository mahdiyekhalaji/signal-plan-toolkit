"""
Write the Yonge and Steeles network straight into a Vissim .inpx file.

The .inpx is XML. This takes the file Vissim 2026 itself wrote (so the version
header, defaults and distributions are exactly what it expects) and inserts the
network: eight links, twelve connectors, four vehicle inputs, four routing
decisions with twelve routes, an evaluation node, and the run settings.

    python write_inpx.py steeles_yonge_A.inpx -o steeles_yonge_built.inpx

Sections are inserted in alphabetical position among the existing top-level
elements, which is the order Vissim writes them in.
"""
from __future__ import annotations

import argparse
import copy
import xml.etree.ElementTree as ET

LANE_W = 3.5
APPROACH_LEN = 250.0
DEPART_LEN = 150.0
SETBACK = 15.0
HALF_NS = 3 * LANE_W          # Yonge, 3 lanes per direction
HALF_EW = 4 * LANE_W          # Steeles, 4 lanes on the wider approach

APPROACH_LANES = {"N": 4, "S": 4, "E": 4, "W": 4}
DEPART_LANES = 3
MOVEMENT_LANES = {"L": [1], "T": [2, 3], "R": [4]}

VOLUMES = {
    ("N", "L"): 134, ("N", "T"): 944, ("N", "R"): 140,
    ("S", "L"): 151, ("S", "T"): 976, ("S", "R"): 52,
    ("E", "L"): 183, ("E", "T"): 771, ("E", "R"): 198,
    ("W", "L"): 254, ("W", "T"): 771, ("W", "R"): 335,
}
DEST = {
    ("N", "L"): "W", ("N", "T"): "N", ("N", "R"): "E",
    ("S", "L"): "E", ("S", "T"): "S", ("S", "R"): "W",
    ("E", "L"): "N", ("E", "T"): "E", ("E", "R"): "S",
    ("W", "L"): "S", ("W", "T"): "W", ("W", "R"): "N",
}

# link numbers
APPR_NO = {"N": 1, "S": 2, "E": 3, "W": 4}
DEP_NO = {"N": 11, "S": 12, "E": 13, "W": 14}


def approach_geom(appr):
    lateral = APPROACH_LANES[appr] * LANE_W / 2.0
    setback = SETBACK + (HALF_EW if appr in "NS" else HALF_NS)
    if appr == "N":
        return (lateral, -(setback + APPROACH_LEN)), (lateral, -setback)
    if appr == "S":
        return (-lateral, setback + APPROACH_LEN), (-lateral, setback)
    if appr == "E":
        return (-(setback + APPROACH_LEN), -lateral), (-setback, -lateral)
    return (setback + APPROACH_LEN, lateral), (setback, lateral)


def depart_geom(appr):
    lateral = DEPART_LANES * LANE_W / 2.0
    setback = SETBACK + (HALF_EW if appr in "NS" else HALF_NS)
    if appr == "N":
        return (lateral, setback), (lateral, setback + DEPART_LEN)
    if appr == "S":
        return (-lateral, -setback), (-lateral, -(setback + DEPART_LEN))
    if appr == "E":
        return (setback, -lateral), (setback + DEPART_LEN, -lateral)
    return (-setback, lateral), (-(setback + DEPART_LEN), lateral)


LINK_ATTRS = {
    "assumSpeedOncomTraffic": "60.000000",
    "behaviorType": "1",
    "costPerKm": "0.000000",
    "displayType": "1",
    "emergStopDist": "5.000000",
    "gradient": "0.000000",
    "hasOvtLn": "false",
    "isPedArea": "false",
    "level": "1",
    "linkEvalAct": "false",
    "linkEvalSegLen": "10.000000",
    "lnChgDist": "200.000000",
    "lnChgDistIsPerLn": "false",
    "lnChgEvalAct": "true",
    "mesoFollowUpGap": "0.000000",
    "mesoReactionTime": "0.000000",
    "ovtOnlyPT": "false",
    "ovtSpeedDiff": "0",
    "showClsValues": "true",
    "surch1": "0.000000",
    "surch2": "0.000000",
    "thickness": "0.000000",
    "vehRecAct": "true",
}


def make_link(no, name, p0, p1, lanes):
    el = ET.Element("link", {**LINK_ATTRS, "name": name, "no": str(no)})
    geom = ET.SubElement(el, "geometry")
    pts = ET.SubElement(geom, "points3D")
    for x, y in (p0, p1):
        ET.SubElement(pts, "point3D", {"x": f"{x:.6f}", "y": f"{y:.6f}",
                                       "zOffset": "0.000000"})
    ln = ET.SubElement(el, "lanes")
    for _ in range(lanes):
        ET.SubElement(ln, "lane", {"blockedVehClasses": "",
                                   "width": f"{LANE_W:.6f}"})
    return el


def make_connector(no, name, from_link, from_lane, from_pos,
                   to_link, to_lane, geom_points):
    """A connector is a link with from/to lane references.

    Vissim reported "the attribute Level may not be set" on connectors, so the
    level attribute is dropped here even though plain links require it.
    """
    attrs = {k: v for k, v in LINK_ATTRS.items() if k != "level"}
    el = ET.Element("link", {**attrs, "name": name, "no": str(no)})
    ET.SubElement(el, "fromLinkEndPt", {"lane": f"{from_link} {from_lane}",
                                        "pos": f"{from_pos:.6f}"})
    ET.SubElement(el, "toLinkEndPt", {"lane": f"{to_link} {to_lane}",
                                      "pos": "0.000000"})
    geom = ET.SubElement(el, "geometry")
    pts = ET.SubElement(geom, "points3D")
    for x, y in geom_points:
        ET.SubElement(pts, "point3D", {"x": f"{x:.6f}", "y": f"{y:.6f}",
                                       "zOffset": "0.000000"})
    ln = ET.SubElement(el, "lanes")
    ET.SubElement(ln, "lane", {"blockedVehClasses": "",
                               "width": f"{LANE_W:.6f}"})
    return el


def build_links():
    links = ET.Element("links")
    for appr in "NSEW":
        p0, p1 = approach_geom(appr)
        links.append(make_link(APPR_NO[appr], f"{appr}B approach", p0, p1,
                               APPROACH_LANES[appr]))
        d0, d1 = depart_geom(appr)
        links.append(make_link(DEP_NO[appr], f"{appr}B departure", d0, d1,
                               DEPART_LANES))

    no = 100
    for (appr, turn), dest in DEST.items():
        src_no = APPR_NO[appr]
        dst_no = DEP_NO[dest]
        _, src_end = approach_geom(appr)
        dst_start, _ = depart_geom(dest)
        from_lanes = MOVEMENT_LANES[turn]
        to_lanes = ([1] if turn == "L" else
                    [DEPART_LANES] if turn == "R" else [2, 3])
        for i, fl in enumerate(from_lanes):
            tl = to_lanes[i % len(to_lanes)]
            mid = ((src_end[0] + dst_start[0]) / 2.0,
                   (src_end[1] + dst_start[1]) / 2.0)
            links.append(make_connector(
                no, f"{appr}B{turn}", src_no, fl, APPROACH_LEN,
                dst_no, tl, [src_end, mid, dst_start]))
            no += 1
    return links


def build_inputs():
    root = ET.Element("vehicleInputs")
    for i, appr in enumerate("NSEW", start=1):
        total = sum(v for (a, t), v in VOLUMES.items() if a == appr)
        vi = ET.SubElement(root, "vehicleInput", {
            "anmFlag": "false", "link": str(APPR_NO[appr]),
            "name": f"{appr}B input", "no": str(i)})
        vols = ET.SubElement(vi, "timeIntervalVehVolumes")
        ET.SubElement(vols, "timeIntervalVehVolume", {
            "cont": "false", "timeInt": "VEHICLEINPUT 0",
            "vehComp": "1", "volume": f"{total}.000000"})
    return root


def build_routes():
    root = ET.Element("vehicleRoutingDecisionsStatic")
    for i, appr in enumerate("NSEW", start=1):
        rd = ET.SubElement(root, "vehicleRoutingDecisionStatic", {
            "allVehTypes": "true", "anmFlag": "false",
            "combineStaticRoutingDecisions": "false",
            "link": str(APPR_NO[appr]), "name": f"{appr}B routing",
            "no": str(i), "pos": f"{APPROACH_LEN - 180.0:.6f}"})
        seq = ET.SubElement(rd, "vehRoutSta")
        for j, turn in enumerate("LTR", start=1):
            dest = DEST[(appr, turn)]
            rt = ET.SubElement(seq, "vehicleRouteStatic", {
                "destLink": str(DEP_NO[dest]),
                "destPos": f"{DEPART_LEN / 2:.6f}",
                "name": f"{appr}B{turn}", "no": str(j)})
            ET.SubElement(rt, "linkSeq")
            flows = ET.SubElement(rt, "relFlows")
            ET.SubElement(flows, "relFlow", {
                "timeInt": "VEHICLEROUTESTATIC 0",
                "relFlow": f"{VOLUMES[(appr, turn)]}.000000"})
    return root


def build_nodes():
    extent = SETBACK + max(HALF_NS, HALF_EW) + 10
    root = ET.Element("nodes")
    nd = ET.SubElement(root, "node", {
        "name": "PX131", "no": "1", "useForEval": "true"})
    poly = ET.SubElement(nd, "polygon")
    pts = ET.SubElement(poly, "points3D")
    for x, y in ((-extent, -extent), (extent, -extent),
                 (extent, extent), (-extent, extent)):
        ET.SubElement(pts, "point3D", {"x": f"{x:.6f}", "y": f"{y:.6f}",
                                       "zOffset": "0.000000"})
    return root


def insert_alphabetical(root, element):
    """Vissim writes top-level sections alphabetically; keep that order."""
    tag = element.tag
    for i, child in enumerate(root):
        if child.tag > tag and child.tag not in ("anmDefaults", "aliases"):
            root.insert(i, element)
            return
    root.append(element)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("template", help="an .inpx written by your Vissim")
    p.add_argument("-o", "--out", default="steeles_yonge_built.inpx")
    args = p.parse_args()

    ET.register_namespace("", "")
    tree = ET.parse(args.template)
    root = tree.getroot()

    for section in ("links", "vehicleInputs", "vehicleRoutingDecisionsStatic",
                    "nodes"):
        old = root.find(section)
        if old is not None:
            root.remove(old)

    insert_alphabetical(root, build_links())
    insert_alphabetical(root, build_inputs())
    insert_alphabetical(root, build_routes())
    insert_alphabetical(root, build_nodes())

    # run settings: 10 runs, 4500 s, first 900 s discarded
    sim = root.find("simulation")
    sim.set("simPeriod", "4500")
    sim.set("numRuns", "10")
    sim.set("randSeed", "42")
    sim.set("randSeedIncr", "1")

    ev = root.find("evaluation")
    nr = ev.find("nodeResults")
    if nr is not None:
        nr.set("collectData", "true")
        nr.set("fromTime", "900")
        nr.set("toTime", "4500")
        nr.set("interval", "3600")

    tree.write(args.out, encoding="UTF-8", xml_declaration=True)
    print(f"wrote {args.out}")
    print("  8 links, 12 connectors, 4 vehicle inputs, 12 routes, 1 node")
    print("  simulation: 10 runs of 4500 s, node results from 900 s")
    print("\nstill to add in Vissim: the signal controller and signal heads")


if __name__ == "__main__":
    main()
