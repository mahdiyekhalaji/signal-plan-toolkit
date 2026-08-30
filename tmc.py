"""
Read a City of Toronto turning movement count and extract peak hour demand.

The raw open data gives 15-minute bins with one column per approach, mode and
movement (e.g. n_appr_cars_l). This module reshapes that into movement volumes,
finds the peak hour by a rolling four-interval sum, and computes the peak hour
factor and heavy vehicle percentage each movement needs for HCM capacity work.

    python tmc.py steeles_yonge_2026-06-30_raw.csv --period pm
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

APPROACHES = ("n", "s", "e", "w")
TURNS = ("l", "t", "r")
MODES = ("cars", "truck", "bus")


@dataclass
class Interval:
    """One 15-minute bin."""
    start: datetime
    vehicles: dict[tuple[str, str], int] = field(default_factory=dict)   # (appr, turn) -> veh
    heavy: dict[tuple[str, str], int] = field(default_factory=dict)      # trucks + buses
    peds: dict[str, int] = field(default_factory=dict)                   # appr -> crossings
    bikes: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.vehicles.values())


def read_count(path: str) -> tuple[list[Interval], dict]:
    intervals, meta = [], {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if not meta:
                meta = {k: row[k] for k in
                        ("count_id", "count_date", "location_name", "px",
                         "latitude", "longitude") if k in row}
            iv = Interval(start=datetime.fromisoformat(row["start_time"]))
            for a in APPROACHES:
                for t in TURNS:
                    by_mode = {m: int(row[f"{a}_appr_{m}_{t}"]) for m in MODES}
                    iv.vehicles[(a, t)] = sum(by_mode.values())
                    iv.heavy[(a, t)] = by_mode["truck"] + by_mode["bus"]
                iv.peds[a] = int(row[f"{a}_appr_peds"])
                iv.bikes[a] = int(row[f"{a}_appr_bike"])
            intervals.append(iv)
    intervals.sort(key=lambda i: i.start)
    return intervals, meta


def peak_hour(intervals: list[Interval], window: tuple[int, int] | None = None
              ) -> list[Interval]:
    """Four consecutive intervals with the highest total volume."""
    pool = intervals
    if window:
        lo, hi = window
        pool = [i for i in intervals if lo <= i.start.hour < hi]
    if len(pool) < 4:
        raise ValueError("not enough intervals in the requested window")
    best, best_i = -1, 0
    for i in range(len(pool) - 3):
        four = pool[i:i + 4]
        gaps = (four[-1].start - four[0].start).total_seconds() / 60
        if gaps != 45:          # skip across a gap in a non-continuous count
            continue
        v = sum(x.total for x in four)
        if v > best:
            best, best_i = v, i
    return pool[best_i:best_i + 4]


def movement_volumes(hour: list[Interval]) -> dict[tuple[str, str], int]:
    out = defaultdict(int)
    for iv in hour:
        for key, v in iv.vehicles.items():
            out[key] += v
    return dict(out)


def phf(hour: list[Interval], key: tuple[str, str] | None = None) -> float:
    """Peak hour factor: hourly volume over four times the busiest 15 minutes."""
    if key is None:
        bins = [iv.total for iv in hour]
    else:
        bins = [iv.vehicles[key] for iv in hour]
    top = max(bins)
    return sum(bins) / (4 * top) if top else 0.0


def heavy_pct(hour: list[Interval], key: tuple[str, str]) -> float:
    veh = sum(iv.vehicles[key] for iv in hour)
    hv = sum(iv.heavy[key] for iv in hour)
    return hv / veh if veh else 0.0


def ped_volumes(hour: list[Interval]) -> dict[str, int]:
    return {a: sum(iv.peds[a] for iv in hour) for a in APPROACHES}


def report(path: str, period: str) -> None:
    intervals, meta = read_count(path)
    window = {"am": (6, 12), "pm": (12, 20), "all": None}[period]
    hour = peak_hour(intervals, window)
    vols = movement_volumes(hour)
    peds = ped_volumes(hour)

    print(f"{meta.get('location_name', path)}")
    print(f"count {meta.get('count_id')}   {meta.get('count_date')}   "
          f"signal px {meta.get('px')}")
    print(f"{period.upper()} peak hour {hour[0].start:%H:%M} to "
          f"{hour[-1].start:%H:%M} +15   total {sum(vols.values())} veh")
    print(f"intersection PHF {phf(hour):.3f}\n")

    name = {"n": "NB", "s": "SB", "e": "EB", "w": "WB"}
    print(f"{'MVMT':<6}{'VOL':>7}{'PHF':>8}{'HV%':>8}")
    for a in APPROACHES:
        for t in TURNS:
            k = (a, t)
            print(f"{name[a] + t.upper():<6}{vols[k]:>7}{phf(hour, k):>8.3f}"
                  f"{heavy_pct(hour, k) * 100:>8.1f}")
        print(f"{'  appr':<6}{sum(vols[(a, t)] for t in TURNS):>7}")
    print(f"\npedestrian crossings, {period.upper()} peak hour")
    for a in APPROACHES:
        print(f"  {name[a][0]} leg {peds[a]:>5}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("count_file")
    p.add_argument("--period", choices=("am", "pm", "all"), default="pm")
    args = p.parse_args()
    report(args.count_file, args.period)


if __name__ == "__main__":
    main()
