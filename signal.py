"""
Phasing, capacity and control delay for one signalized intersection.

Replaces the two-phase Webster placeholder with a ring-barrier structure,
protected or protected-permitted left turns, and HCM 6th Edition control delay
by movement, approach and intersection.

    python signal.py data/steeles_yonge_2026-06-30_raw.csv \
        config/steeles_yonge.json --period pm --compare

Method notes, with the equation each piece comes from, so they can be checked:
  capacity            c = s * g/C                          HCM Eq 19-16
  uniform delay       d1 = 0.5C(1-g/C)^2 / (1-[min(1,X)]g/C)  HCM Eq 19-18
  incremental delay   d2 = 900T[(X-1) + sqrt((X-1)^2 + 8kIX/(cT))]  HCM Eq 19-26
  progression factor  PF applied to d1 for coordinated movements
  level of service    by control delay, HCM Exhibit 19-8

Permitted left capacity uses a gap-acceptance estimate rather than the full
HCM permitted-phase procedure. That is the weakest step here and is flagged in
the output.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field

import hcm
import tmc

LOST_TIME = 4.0        # s per phase: startup lost time plus clearance not used
YELLOW = 3.5
ALL_RED = 1.5
MIN_GREEN_VEH = 8.0
ANALYSIS_T = 0.25      # h, analysis period for the incremental delay term

LOS_BREAKS = ((10, "A"), (20, "B"), (35, "C"), (55, "D"), (80, "E"))


def los(delay: float) -> str:
    for limit, letter in LOS_BREAKS:
        if delay <= limit:
            return letter
    return "F"


# ------------------------------------------------------------------ phasing
@dataclass
class Movement:
    key: str                  # e.g. "NBL"
    approach: str             # n s e w
    turn: str                 # l t r
    volume: float
    sat: float                # saturation flow, veh/h
    protected: bool = True
    permitted: bool = False
    lanes: int = 1
    green: float = 0.0
    coordinated: bool = False

    @property
    def flow_ratio(self) -> float:
        return self.volume / self.sat if self.sat else 0.0


@dataclass
class Phase:
    name: str
    movements: list[str]
    critical_ratio: float = 0.0
    green: float = 0.0

    @property
    def lost(self) -> float:
        return LOST_TIME


@dataclass
class Plan:
    """A NEMA-style two-ring, two-barrier plan."""
    label: str
    cycle: float
    phases: list[Phase]
    left_treatment: str = "protected"      # protected | prot-perm
    notes: str = ""


NAMES = {"n": "NB", "s": "SB", "e": "EB", "w": "WB"}


def build_movements(count_file: str, cfg_path: str, period: str
                    ) -> tuple[list[Movement], dict, list]:
    intervals, meta = tmc.read_count(count_file)
    window = {"am": (6, 12), "pm": (12, 20), "all": None}[period]
    hour = tmc.peak_hour(intervals, window)
    vols = tmc.movement_volumes(hour)
    peds = tmc.ped_volumes(hour)
    bikes = {a: sum(iv.bikes[a] for iv in hour) for a in tmc.APPROACHES}
    conflict = {"n": "e", "e": "s", "s": "w", "w": "n"}

    movements = []
    for lg in hcm.load_config(cfg_path):
        v = sum(vols[(lg.approach, m)] for m in lg.movements)
        hv = (sum(sum(iv.heavy[(lg.approach, m)] for m in lg.movements)
                  for iv in hour) / v) if v else 0.0
        leg = conflict[lg.approach]
        s, _ = hcm.saturation_flow(lg, hv, peds[leg], bikes[leg])
        turn = "l" if lg.movements == "l" else ("r" if lg.movements == "r" else "t")
        movements.append(Movement(
            key=NAMES[lg.approach] + lg.movements.upper(),
            approach=lg.approach, turn=turn, volume=v, sat=s, lanes=lg.lanes))
    return movements, meta, hour


def ring_barrier(movements: list[Movement], left_treatment: str) -> list[Phase]:
    """Standard eight-phase structure collapsed to the four critical phases.

    Barrier 1 covers the north-south street, barrier 2 the east-west street.
    Each barrier has a leading protected left phase and a through phase.
    """
    ns_left = [m.key for m in movements if m.approach in "ns" and m.turn == "l"]
    ns_thru = [m.key for m in movements if m.approach in "ns" and m.turn != "l"]
    ew_left = [m.key for m in movements if m.approach in "ew" and m.turn == "l"]
    ew_thru = [m.key for m in movements if m.approach in "ew" and m.turn != "l"]

    phases = []
    if ns_left and left_treatment != "permitted":
        phases.append(Phase("1  NS left (protected)", ns_left))
    phases.append(Phase("2  NS through", ns_thru))
    if ew_left and left_treatment != "permitted":
        phases.append(Phase("3  EW left (protected)", ew_left))
    phases.append(Phase("4  EW through", ew_thru))
    return phases


def permitted_capacity(opposing_flow: float, green: float, cycle: float) -> float:
    """Gap-acceptance estimate for a permitted left, veh/h.

    Simplified: critical gap 4.5 s, follow-up 2.5 s, exponential headways in the
    opposing stream. The HCM procedure is more involved and this will differ
    from it, particularly at high opposing volumes.
    """
    if opposing_flow <= 0:
        return 1400.0
    q = opposing_flow / 3600.0
    tc, tf = 4.5, 2.5
    cap = q * math.exp(-q * tc) / (1 - math.exp(-q * tf)) * 3600.0
    return max(0.0, cap * green / cycle)


def critical_ratios(movements: list[Movement], phases: list[Phase]) -> float:
    by_key = {m.key: m for m in movements}
    y_sum = 0.0
    for ph in phases:
        ph.critical_ratio = max((by_key[k].flow_ratio for k in ph.movements
                                 if k in by_key), default=0.0)
        y_sum += ph.critical_ratio
    return y_sum


def webster_cycle(y_sum: float, n_phases: int) -> float:
    L = LOST_TIME * n_phases
    if y_sum >= 0.95:
        return 180.0
    return (1.5 * L + 5.0) / (1.0 - y_sum)


def allocate(movements: list[Movement], phases: list[Phase], cycle: float) -> None:
    y_sum = critical_ratios(movements, phases)
    total_lost = LOST_TIME * len(phases)
    effective = max(cycle - total_lost, 8.0 * len(phases))
    for ph in phases:
        share = ph.critical_ratio / y_sum if y_sum else 1.0 / len(phases)
        ph.green = max(MIN_GREEN_VEH, effective * share)
    scale = effective / sum(p.green for p in phases)
    for ph in phases:
        ph.green *= scale
    by_key = {m.key: m for m in movements}
    for m in movements:
        m.green = 0.0
    for ph in phases:
        for k in ph.movements:
            if k in by_key:
                by_key[k].green += ph.green


def delays(movements: list[Movement], cycle: float, plan: Plan,
           progression: float = 1.0) -> dict:
    """HCM control delay by movement, approach and intersection."""
    out = {}
    for m in movements:
        g_c = m.green / cycle if cycle else 0.0
        cap = m.sat * g_c
        if plan.left_treatment == "prot-perm" and m.turn == "l":
            opp = sum(o.volume for o in movements
                      if o.approach in _opposing(m.approach) and o.turn == "t")
            cap += permitted_capacity(opp, cycle - m.green, cycle)
        X = m.volume / cap if cap else 9.9
        d1 = (0.5 * cycle * (1 - g_c) ** 2) / max(1e-6, 1 - min(1.0, X) * g_c)
        pf = progression if m.coordinated else 1.0
        k, I = 0.5, 1.0
        d2 = 900 * ANALYSIS_T * ((X - 1) + math.sqrt(max(0.0, (X - 1) ** 2
             + (8 * k * I * X) / max(cap * ANALYSIS_T, 1e-6))))
        d = d1 * pf + d2
        out[m.key] = {"v": m.volume, "s": round(m.sat), "g": round(m.green, 1),
                      "c": round(cap), "X": round(X, 3), "d": round(d, 1),
                      "los": los(d)}
    # approach and intersection aggregation, volume weighted
    appr = {}
    for a in "nsew":
        ms = [m for m in movements if m.approach == a]
        v = sum(m.volume for m in ms)
        if not v:
            continue
        d = sum(out[m.key]["d"] * m.volume for m in ms) / v
        appr[NAMES[a]] = {"v": v, "d": round(d, 1), "los": los(d)}
    v_tot = sum(m.volume for m in movements)
    d_tot = sum(out[m.key]["d"] * m.volume for m in movements) / v_tot if v_tot else 0
    return {"movements": out, "approaches": appr,
            "intersection": {"v": v_tot, "d": round(d_tot, 1), "los": los(d_tot)}}


def _opposing(a: str) -> str:
    return {"n": "s", "s": "n", "e": "w", "w": "e"}[a]


def make_plan(movements: list[Movement], label: str, left_treatment: str,
              cycle: float | None = None, coordinate: str = "ns") -> tuple[Plan, dict]:
    phases = ring_barrier(movements, left_treatment)
    y = critical_ratios(movements, phases)
    c = cycle or math.ceil(webster_cycle(y, len(phases)) / 5) * 5
    c = min(max(c, 50.0), 180.0)
    allocate(movements, phases, c)
    for m in movements:
        m.coordinated = m.approach in coordinate and m.turn != "l"
    plan = Plan(label, c, phases, left_treatment)
    res = delays(movements, c, plan, progression=0.85 if coordinate else 1.0)
    return plan, res


def timing_sheet(plan: Plan, movements: list[Movement]) -> str:
    """Splits sum to the cycle.

    green interval = split - yellow - all red, and effective green = split minus
    the 4 s lost per phase (2 s start-up, 2 s of the clearance interval unused).
    """
    lines = [f"{plan.label}   cycle {plan.cycle:.0f} s   "
             f"left turns {plan.left_treatment}", ""]
    lines.append(f"{'PHASE':<26}{'SPLIT':>8}{'GREEN':>8}{'YELLOW':>8}"
                 f"{'ALL RED':>9}{'g eff':>8}")
    for ph in plan.phases:
        split = ph.green + LOST_TIME
        g_int = split - YELLOW - ALL_RED
        lines.append(f"{ph.name:<26}{split:>8.1f}{g_int:>8.1f}{YELLOW:>8.1f}"
                     f"{ALL_RED:>9.1f}{ph.green:>8.1f}")
    total = sum(p.green + LOST_TIME for p in plan.phases)
    lines.append(f"{'total':<26}{total:>8.1f}")
    return "\n".join(lines)


def report(res: dict, title: str) -> str:
    lines = [title, ""]
    lines.append(f"{'MVMT':<8}{'VOL':>7}{'s':>7}{'g':>7}{'CAP':>7}{'v/c':>8}"
                 f"{'DELAY':>8}{'LOS':>5}")
    for k, r in res["movements"].items():
        lines.append(f"{k:<8}{r['v']:>7.0f}{r['s']:>7}{r['g']:>7.1f}{r['c']:>7}"
                     f"{r['X']:>8.3f}{r['d']:>8.1f}{r['los']:>5}")
    lines.append("")
    for k, r in res["approaches"].items():
        lines.append(f"{k+' approach':<22}{r['v']:>7.0f} veh"
                     f"{r['d']:>8.1f} s{r['los']:>5}")
    i = res["intersection"]
    lines.append(f"{'INTERSECTION':<22}{i['v']:>7.0f} veh{i['d']:>8.1f} s{i['los']:>5}")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("count_file")
    p.add_argument("config")
    p.add_argument("--period", choices=("am", "pm", "all"), default="pm")
    p.add_argument("--cycle", type=float, help="force a cycle length")
    p.add_argument("--compare", action="store_true",
                   help="run the alternatives and print the comparison")
    p.add_argument("--json", help="write the full results to this path")
    args = p.parse_args()

    movements, meta, hour = build_movements(args.count_file, args.config, args.period)
    head = (f"{meta.get('location_name')}   {args.period.upper()} peak "
            f"{hour[0].start:%H:%M}")

    if not args.compare:
        plan, res = make_plan(movements, "Base plan", "protected", args.cycle)
        print(head, "\n")
        print(timing_sheet(plan, movements), "\n")
        print(report(res, "Capacity and control delay"))
        return

    alts = [
        ("A  Protected lefts, Webster cycle", "protected", None),
        ("B  Protected lefts, 100 s cycle", "protected", 100.0),
        ("C  Protected-permitted lefts, Webster cycle", "prot-perm", None),
        ("D  Protected-permitted lefts, 120 s cycle", "prot-perm", 120.0),
    ]
    results = []
    print(head, "\n")
    for label, treat, cyc in alts:
        movements, _, _ = build_movements(args.count_file, args.config, args.period)
        plan, res = make_plan(movements, label, treat, cyc)
        results.append((plan, res))
        print(timing_sheet(plan, movements))
        print()
        print(report(res, "  " + label))
        print("\n" + "-" * 62 + "\n")

    print(f"{'ALTERNATIVE':<44}{'CYCLE':>7}{'DELAY':>8}{'LOS':>5}{'WORST v/c':>11}")
    for plan, res in results:
        worst = max(r["X"] for r in res["movements"].values())
        i = res["intersection"]
        print(f"{plan.label:<44}{plan.cycle:>7.0f}{i['d']:>8.1f}{i['los']:>5}"
              f"{worst:>11.3f}")

    if args.json:
        out = [{"label": p.label, "cycle": p.cycle,
                "left_treatment": p.left_treatment,
                "phases": [{"name": ph.name, "green": round(ph.green, 1),
                            "yellow": YELLOW, "all_red": ALL_RED}
                           for ph in p.phases],
                "results": r} for p, r in results]
        json.dump(out, open(args.json, "w"), indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
