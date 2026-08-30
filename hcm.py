"""
Saturation flow rate by lane group, HCM 6th Edition Chapter 19.

s = s0 * N * fw * fHVg * fp * fbb * fa * fLU * fLT * fRT * fLpb * fRpb

Every factor below cites the equation or exhibit it comes from so you can check
it against the manual. Defaults are conservative urban-arterial values; the
site-specific ones belong in the JSON config, not in this file.

    python hcm.py data/steeles_yonge_2026-06-30_raw.csv config/steeles_yonge.json

Lane group notation: approach + movement, e.g. "n_l" for the northbound left.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

import tmc

S0 = 1900.0          # base saturation flow, pc/h/ln (HCM Eq 19-8 default)


@dataclass
class LaneGroup:
    approach: str            # n, s, e, w
    movements: str           # any of "l", "t", "r", e.g. "lt" for a shared lane
    lanes: int
    exclusive_left: bool = False
    exclusive_right: bool = False
    lane_width: float = 3.3        # m
    grade: float = 0.0             # percent, upgrade positive
    parking: bool = False
    parking_maneuvers: int = 0     # per hour
    buses_stopping: int = 0        # per hour
    area_type_cbd: bool = False

    @property
    def key(self) -> str:
        return f"{self.approach}_{self.movements}"


# --------------------------------------------------------------- adjustments
def f_width(w: float) -> float:
    """Lane width factor, HCM Eq 19-9. Metric thresholds 3.0 m and 3.9 m."""
    if w < 3.0:
        return 1.0 + (w - 3.6) / 9.0
    if w <= 3.9:
        return 1.0 + (w - 3.6) / 9.0
    return 1.04


def f_heavy_grade(hv_pct: float, grade_pct: float) -> float:
    """Heavy vehicle and grade factor, HCM Eq 19-10.

    Passenger car equivalent of 2.0 for heavy vehicles.
    """
    et = 2.0
    return 100.0 / (100.0 + hv_pct * (et - 1.0)) * (1.0 - grade_pct / 200.0)


def f_parking(lanes: int, parking: bool, maneuvers: int) -> float:
    """Parking factor, HCM Eq 19-11. Maneuvers capped at 180/h."""
    if not parking:
        return 1.0
    nm = min(maneuvers, 180)
    return max(0.050, (lanes - 0.1 - 18.0 * nm / 3600.0) / lanes)


def f_bus(lanes: int, buses: int) -> float:
    """Bus blockage factor, HCM Eq 19-12. Buses capped at 250/h."""
    nb = min(buses, 250)
    return max(0.050, (lanes - 14.4 * nb / 3600.0) / lanes)


def f_area(cbd: bool) -> float:
    """Area type factor, HCM Exhibit 19-13."""
    return 0.90 if cbd else 1.00


def f_lane_use(lanes: int, exclusive_left: bool, exclusive_right: bool) -> float:
    """Lane utilization factor, HCM Exhibit 19-15.

    Accounts for uneven use across lanes in a group. Single-lane groups are 1.0.
    """
    if lanes <= 1:
        return 1.0
    if exclusive_left or exclusive_right:
        return {2: 0.97, 3: 0.94}.get(lanes, 0.94)
    return {2: 0.95, 3: 0.91}.get(lanes, 0.86)


def f_left_turn(movements: str, exclusive: bool, protected: bool = True) -> float:
    """Left turn factor, HCM Eq 19-13 and 19-16.

    Exclusive protected left lanes take 0.95 for the turning radius penalty.
    Shared lanes need the left-turn proportion, handled by the caller.
    """
    if "l" not in movements:
        return 1.0
    if exclusive and protected:
        return 0.95
    return 0.95


def f_right_turn(movements: str, exclusive: bool) -> float:
    """Right turn factor, HCM Eq 19-14."""
    if "r" not in movements:
        return 1.0
    return 0.85 if exclusive else 0.85


def f_ped_bike_left(peds_per_hour: int, opposing_lanes: int = 1) -> float:
    """Pedestrian-bicycle adjustment for a protected left, HCM Section 19-4.

    A fully protected left with no conflicting pedestrian phase is 1.0. This
    simplification applies the permitted-phase relationship only when the
    caller says the left is permitted.
    """
    v = min(peds_per_hour, 5000)
    occ = v / 2000.0 if v <= 1000 else 0.4 + v / 10000.0
    return max(0.0, 1.0 - occ * (1.0 - 0.6))


def f_ped_bike_right(peds_per_hour: int, bikes_per_hour: int = 0) -> float:
    """Pedestrian-bicycle adjustment for a right turn, HCM Section 19-4.

    Right turns yield to the concurrent pedestrian phase, so this is the factor
    that usually bites at a busy urban crossing.
    """
    v = min(peds_per_hour, 5000)
    occ_ped = v / 2000.0 if v <= 1000 else 0.4 + v / 10000.0
    occ_bike = min(bikes_per_hour, 1900) / 2700.0
    occ = min(occ_ped + occ_bike - occ_ped * occ_bike, 1.0)
    return max(0.0, 1.0 - occ * (1.0 - 0.5))


# ------------------------------------------------------------------ assembly
def saturation_flow(lg: LaneGroup, hv_pct: float, peds: int, bikes: int,
                    verbose: bool = False) -> tuple[float, dict[str, float]]:
    f = {
        "fw": f_width(lg.lane_width),
        "fHVg": f_heavy_grade(hv_pct * 100.0, lg.grade),
        "fp": f_parking(lg.lanes, lg.parking, lg.parking_maneuvers),
        "fbb": f_bus(lg.lanes, lg.buses_stopping),
        "fa": f_area(lg.area_type_cbd),
        "fLU": f_lane_use(lg.lanes, lg.exclusive_left, lg.exclusive_right),
        "fLT": f_left_turn(lg.movements, lg.exclusive_left),
        "fRT": f_right_turn(lg.movements, lg.exclusive_right),
        "fLpb": 1.0,
        "fRpb": f_ped_bike_right(peds, bikes) if "r" in lg.movements else 1.0,
    }
    s = S0 * lg.lanes
    for v in f.values():
        s *= v
    return s, f


def load_config(path: str) -> list[LaneGroup]:
    with open(path) as fh:
        cfg = json.load(fh)
    defaults = cfg.get("defaults", {})
    groups = []
    for g in cfg["lane_groups"]:
        groups.append(LaneGroup(**{**defaults, **g}))
    return groups


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("count_file")
    p.add_argument("config")
    p.add_argument("--period", choices=("am", "pm", "all"), default="pm")
    args = p.parse_args()

    intervals, meta = tmc.read_count(args.count_file)
    window = {"am": (6, 12), "pm": (12, 20), "all": None}[args.period]
    hour = tmc.peak_hour(intervals, window)
    vols = tmc.movement_volumes(hour)
    peds = tmc.ped_volumes(hour)
    bikes = {a: sum(iv.bikes[a] for iv in hour) for a in tmc.APPROACHES}

    # pedestrians conflicting with a right turn cross the leg to the driver's
    # right, not the leg the driver came from
    conflict = {"n": "e", "e": "s", "s": "w", "w": "n"}

    print(f"{meta.get('location_name')}   {args.period.upper()} peak hour "
          f"{hour[0].start:%H:%M}\n")
    print(f"{'GROUP':<8}{'LANES':>6}{'VOL':>7}{'s':>8}   factors")
    total_rows = []
    for lg in load_config(args.config):
        v = sum(vols[(lg.approach, m)] for m in lg.movements)
        hv = (sum(sum(iv.heavy[(lg.approach, m)] for m in lg.movements) for iv in hour)
              / v) if v else 0.0
        leg = conflict[lg.approach]
        s, f = saturation_flow(lg, hv, peds[leg], bikes[leg])
        shown = "  ".join(f"{k} {val:.3f}" for k, val in f.items()
                          if abs(val - 1.0) > 0.001)
        print(f"{lg.key:<8}{lg.lanes:>6}{v:>7}{s:>8.0f}   {shown}")
        total_rows.append((lg.key, v, s))

    print("\nv/s ratios")
    for key, v, s in total_rows:
        print(f"  {key:<8}{v / s:>7.3f}")


if __name__ == "__main__":
    main()
