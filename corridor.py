"""
Corridor progression analysis for a coordinated arterial.

Takes turning movement counts for a string of signals, estimates a Webster
cycle and green splits at each one, then searches for the offset set that
maximizes two-way through bandwidth. Produces a time-space diagram.

    python corridor.py data/yonge_corridor_raw.csv corridor/yonge.json --period pm

Assumptions worth naming: splits come from a two-phase Webster estimate using
critical lane volumes, not from a phasing plan supplied by the operating
agency, and saturation flow is a flat per-lane value rather than the full HCM
adjustment. Both are placeholders until the real timing sheets arrive.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field

import tmc

SAT_PER_LANE = 1800.0     # veh/h/ln, placeholder pending the HCM module
LOST_TIME = 4.0           # s per phase, startup plus clearance
GRID = 0.25               # s, resolution of the bandwidth search


# ------------------------------------------------------------------ geometry
def haversine(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class Signal:
    name: str
    count_id: str
    px: str
    lat: float
    lon: float
    arterial: str = "ns"          # "ns" if the coordinated street runs north-south
    through_lanes: int = 3        # per direction on the arterial
    cross_lanes: int = 2
    radius: float = 12.0
    median_ns: float = 0.0        # raised median on the arterial
    median_ew: float = 0.0        # raised median on the cross street
    chainage: float = 0.0         # m from the first signal
    cycle: float = 0.0
    green_art: float = 0.0        # effective green for the arterial through
    offset: float = 0.0
    volumes: dict = field(default_factory=dict)


def build_signals(count_file: str, cfg_path: str, period: str) -> list[Signal]:
    with open(cfg_path) as fh:
        cfg = json.load(fh)

    intervals_all, _ = tmc.read_count(count_file)          # mixed count_ids
    by_id: dict[str, list] = {}
    import csv
    with open(count_file, newline="") as fh:
        rows = list(csv.DictReader(fh))
    ids = [r["count_id"] for r in rows]
    for iv, cid in zip(sorted(intervals_all, key=lambda i: i.start), ids):
        pass   # placeholder, real split happens below

    signals = []
    window = {"am": (6, 12), "pm": (12, 20), "all": None}[period]
    for entry in cfg["signals"]:
        sub = [r for r in rows if r["count_id"] == entry["count_id"]]
        ivs = _intervals_from_rows(sub)
        hour = tmc.peak_hour(ivs, window)
        vols = tmc.movement_volumes(hour)
        s = Signal(name=entry["name"], count_id=entry["count_id"],
                   px=entry.get("px", ""), lat=float(sub[0]["latitude"]),
                   lon=float(sub[0]["longitude"]),
                   through_lanes=entry.get("through_lanes", 3),
                   cross_lanes=entry.get("cross_lanes", 2),
                   radius=entry.get("radius", 12.0),
                   median_ns=entry.get("median_ns", 0.0),
                   median_ew=entry.get("median_ew", 0.0),
                   volumes=vols)
        signals.append(s)

    signals.sort(key=lambda s: -s.lat)          # north to south
    origin = signals[0]
    for s in signals:
        s.chainage = haversine(origin.lat, origin.lon, s.lat, s.lon)
    return signals


def _intervals_from_rows(rows) -> list[tmc.Interval]:
    from datetime import datetime
    out = []
    for row in rows:
        iv = tmc.Interval(start=datetime.fromisoformat(row["start_time"]))
        for a in tmc.APPROACHES:
            for t in tmc.TURNS:
                by_mode = {m: int(row[f"{a}_appr_{m}_{t}"]) for m in tmc.MODES}
                iv.vehicles[(a, t)] = sum(by_mode.values())
                iv.heavy[(a, t)] = by_mode["truck"] + by_mode["bus"]
            iv.peds[a] = int(row[f"{a}_appr_peds"])
            iv.bikes[a] = int(row[f"{a}_appr_bike"])
        out.append(iv)
    out.sort(key=lambda i: i.start)
    return out


# -------------------------------------------------------------------- timing
def webster(signals: list[Signal], phases: int = 2) -> float:
    """Common cycle length: the longest Webster optimum across the corridor.

    C = (1.5 L + 5) / (1 - Y), HCM/Webster form, with Y the sum of critical
    flow ratios. Coordination needs one cycle for all signals, so the binding
    intersection sets it.
    """
    worst = 0.0
    for s in signals:
        y_art = max(_crit(s, "n"), _crit(s, "s")) / SAT_PER_LANE / s.through_lanes
        y_cross = max(_crit(s, "e"), _crit(s, "w")) / SAT_PER_LANE / s.cross_lanes
        y = y_art + y_cross
        if y >= 0.95:
            c = 180.0
        else:
            c = (1.5 * LOST_TIME * phases + 5.0) / (1.0 - y)
        s.cycle = min(max(c, 60.0), 180.0)
        worst = max(worst, s.cycle)
    common = min(max(math.ceil(worst / 5) * 5, 60), 180)
    for s in signals:
        s.cycle = common
    return common


def _crit(s: Signal, appr: str) -> float:
    """Critical movement volume on an approach: through plus right, plus left."""
    return sum(s.volumes.get((appr, t), 0) for t in ("t", "r", "l"))


def splits(signals: list[Signal], phases: int = 2) -> None:
    """Effective green for the arterial through, proportional to critical ratios."""
    for s in signals:
        y_art = max(_crit(s, "n"), _crit(s, "s")) / SAT_PER_LANE / s.through_lanes
        y_cross = max(_crit(s, "e"), _crit(s, "w")) / SAT_PER_LANE / s.cross_lanes
        y = y_art + y_cross
        avail = s.cycle - LOST_TIME * phases
        share = y_art / y if y else 0.5
        s.green_art = max(8.0, avail * share)


# --------------------------------------------------------------- progression
def green_mask(s: Signal, cycle: float) -> list[bool]:
    n = int(round(cycle / GRID))
    mask = [False] * n
    start = s.offset % cycle
    for k in range(int(round(s.green_art / GRID))):
        mask[int((start / GRID + k) % n)] = True
    return mask


def band(signals: list[Signal], cycle: float, speed: float, inbound: bool) -> float:
    """Through band width in seconds, for one direction.

    A departure time at the first signal is feasible if the vehicle arrives at
    every downstream signal during its green. Feasible departure times form the
    band; its width is what matters.
    """
    n = int(round(cycle / GRID))
    ref = signals[-1] if inbound else signals[0]
    feasible = [True] * n
    for s in signals:
        dist = abs(s.chainage - ref.chainage)
        tt = dist / speed
        mask = green_mask(s, cycle)
        shift = int(round(tt / GRID))
        for t in range(n):
            if not mask[(t + shift) % n]:
                feasible[t] = False
    return _longest_run(feasible) * GRID


def _longest_run(mask: list[bool]) -> int:
    n = len(mask)
    if all(mask):
        return n
    best = run = 0
    for t in range(2 * n):
        if mask[t % n]:
            run += 1
            best = max(best, min(run, n))
        else:
            run = 0
    return best


def optimize_offsets(signals: list[Signal], cycle: float, speed: float,
                     restarts: int = 12, seed: int = 7) -> tuple[float, float]:
    """Local search over offsets, maximizing the smaller of the two bands.

    Starting point is the ideal outbound progression, offsets set to travel
    time from the reference signal. That gives a full outbound band and
    whatever inbound band falls out of the geometry.
    """
    import random
    rng = random.Random(seed)

    def score():
        out = band(signals, cycle, speed, inbound=False)
        inb = band(signals, cycle, speed, inbound=True)
        return min(out, inb) * 2 + (out + inb) * 0.5, out, inb

    ref = signals[0]
    for s in signals:
        s.offset = ((s.chainage - ref.chainage) / speed) % cycle
    best_offsets = [s.offset for s in signals]
    best, bo, bi = score()

    for r in range(restarts):
        if r:
            for s in signals[1:]:
                s.offset = rng.uniform(0, cycle)
        cur, _, _ = score()
        improved = True
        while improved:
            improved = False
            for i, s in enumerate(signals[1:], start=1):
                base = s.offset
                for step in (8.0, 3.0, 1.0):
                    for d in (step, -step):
                        s.offset = (base + d) % cycle
                        trial, _, _ = score()
                        if trial > cur + 1e-9:
                            cur, base, improved = trial, s.offset, True
                        else:
                            s.offset = base
        if cur > best:
            best, bo, bi = score()
            best_offsets = [s.offset for s in signals]

    for s, o in zip(signals, best_offsets):
        s.offset = o
    return bo, bi


# ---------------------------------------------------------------- reporting
def time_space_plot(signals: list[Signal], cycle: float, speed: float,
                    out_path: str, cycles: int = 3) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 7))
    span = cycle * cycles

    for s in signals:
        y = s.chainage
        t = s.offset % cycle - cycle
        while t < span:
            ax.plot([max(t, 0), min(t + s.green_art, span)], [y, y],
                    lw=6, color="#3f8f5a", solid_capstyle="butt", zorder=3)
            red0 = t + s.green_art
            ax.plot([max(red0, 0), min(t + cycle, span)], [y, y],
                    lw=6, color="#c0392b", alpha=0.75, solid_capstyle="butt", zorder=3)
            t += cycle
        ax.text(-cycle * 0.02, y, f"{s.name}  ", ha="right", va="center", fontsize=8)

    # bands
    for inbound, color in ((False, "#2c6fbb"), (True, "#8e44ad")):
        w = band(signals, cycle, speed, inbound)
        if w <= 0:
            continue
        ref = signals[-1] if inbound else signals[0]
        n = int(round(cycle / GRID))
        feasible = [True] * n
        for s in signals:
            tt = abs(s.chainage - ref.chainage) / speed
            mask = green_mask(s, cycle)
            shift = int(round(tt / GRID))
            for t in range(n):
                if not mask[(t + shift) % n]:
                    feasible[t] = False
        start = _run_start(feasible) * GRID
        for k in range(cycles + 1):
            t0 = start + k * cycle
            for edge in (0.0, w):
                xs, ys = [], []
                for s in (signals if not inbound else list(reversed(signals))):
                    tt = abs(s.chainage - ref.chainage) / speed
                    xs.append(t0 + edge + tt)
                    ys.append(s.chainage)
                ax.plot(xs, ys, color=color, lw=1.2, alpha=0.9, zorder=4)
            xs_a, xs_b, ys = [], [], []
            for s in (signals if not inbound else list(reversed(signals))):
                tt = abs(s.chainage - ref.chainage) / speed
                xs_a.append(t0 + tt)
                xs_b.append(t0 + w + tt)
                ys.append(s.chainage)
            ax.fill_betweenx(ys, xs_a, xs_b, color=color, alpha=0.16, zorder=2)

    ax.set_xlim(0, span)
    ax.set_ylim(-60, signals[-1].chainage + 60)
    ax.invert_yaxis()
    ax.set_xlabel("time (s)")
    ax.set_ylabel("distance from north end (m)")
    ax.set_title(f"Time-space diagram, cycle {cycle:.0f} s, "
                 f"progression speed {speed * 3.6:.0f} km/h")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)


def _run_start(mask: list[bool]) -> int:
    n = len(mask)
    best_len = best_start = 0
    t = 0
    while t < 2 * n:
        if mask[t % n]:
            start, run = t, 0
            while run < n and mask[(t) % n]:
                run += 1
                t += 1
            if run > best_len:
                best_len, best_start = run, start % n
        else:
            t += 1
    return best_start


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("count_file")
    p.add_argument("config")
    p.add_argument("--period", choices=("am", "pm", "all"), default="pm")
    p.add_argument("--speed", type=float, default=50.0, help="progression speed, km/h")
    p.add_argument("--plot", default="output/time-space.png")
    args = p.parse_args()

    signals = build_signals(args.count_file, args.config, args.period)
    speed = args.speed / 3.6
    cycle = webster(signals)
    splits(signals)
    out_b, in_b = optimize_offsets(signals, cycle, speed)

    print(f"corridor: {signals[0].name} to {signals[-1].name}")
    print(f"length {signals[-1].chainage:.0f} m, {len(signals)} signals, "
          f"common cycle {cycle:.0f} s\n")
    print(f"{'SIGNAL':<34}{'CHAIN':>7}{'g_art':>7}{'OFFSET':>8}")
    for s in signals:
        print(f"{s.name[:33]:<34}{s.chainage:>7.0f}{s.green_art:>7.1f}{s.offset:>8.1f}")

    eff_out = out_b / cycle * 100
    eff_in = in_b / cycle * 100
    print(f"\nsouthbound band {out_b:.1f} s  ({eff_out:.0f}% of cycle)")
    print(f"northbound band {in_b:.1f} s  ({eff_in:.0f}% of cycle)")
    print(f"two-way efficiency {(eff_out + eff_in) / 2:.0f}%")

    time_space_plot(signals, cycle, speed, args.plot)
    print(f"\nwrote {args.plot}")


if __name__ == "__main__":
    main()
