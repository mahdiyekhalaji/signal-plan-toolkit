"""
Bridge between the analytical timing plans and a PTV Vissim model.

Two directions:

  export   write each timing alternative as a fixed-time controller sheet plus
           the vehicle inputs and turning fractions the model needs, so the
           model is built from the same numbers the HCM calculation used

  compare  read a Vissim node evaluation export and place the simulated delay
           and queue beside the HCM estimate, movement by movement

    python vissim.py export data/steeles_yonge_2026-06-30_raw.csv \\
        config/steeles_yonge.json --period pm --out vissim/
    python vissim.py compare vissim/node_results.att \\
        output/timing-alternatives.json --alt A

The comparison is the point. HCM and a microsimulation disagreeing is normal;
where they disagree and by how much is the finding worth writing up.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re

import signal as sig
import tmc

MOVEMENT_ORDER = ["NBL", "NBT", "NBR", "SBL", "SBT", "SBR",
                  "EBL", "EBT", "EBR", "WBL", "WBT", "WBR"]


# --------------------------------------------------------------- export side
def export(count_file: str, config: str, period: str, outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    movements, meta, hour = sig.build_movements(count_file, config, period)

    # vehicle inputs by approach, and turning fractions within each approach
    rows = []
    for a in "nsew":
        ms = [m for m in movements if m.approach == a]
        total = sum(m.volume for m in ms)
        for m in ms:
            rows.append({"approach": sig.NAMES[a], "movement": m.key,
                         "volume_vph": round(m.volume),
                         "fraction": round(m.volume / total, 4) if total else 0,
                         "approach_total_vph": round(total)})
    with open(os.path.join(outdir, "vehicle_inputs.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # pedestrian volumes, for a model with pedestrian conflicts or LPI testing
    peds = tmc.ped_volumes(hour)
    with open(os.path.join(outdir, "pedestrian_inputs.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["leg", "crossings_per_hour"])
        for a, v in peds.items():
            w.writerow([sig.NAMES[a][0], v])

    # one controller sheet per alternative
    alts = [("A", "protected", None), ("B", "protected", 100.0),
            ("C", "prot-perm", None), ("D", "prot-perm", 120.0)]
    index = []
    for tag, treat, cycle in alts:
        mv, _, _ = sig.build_movements(count_file, config, period)
        plan, res = sig.make_plan(mv, f"Alternative {tag}", treat, cycle)
        path = os.path.join(outdir, f"controller_{tag}.csv")
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["# fixed-time controller, seconds"])
            w.writerow(["# cycle", f"{plan.cycle:.0f}", "left turns", treat])
            w.writerow(["phase", "movements", "split", "green_interval",
                        "yellow", "all_red", "effective_green"])
            t = 0.0
            for ph in plan.phases:
                split = ph.green + sig.LOST_TIME
                w.writerow([ph.name, " ".join(ph.movements), f"{split:.1f}",
                            f"{split - sig.YELLOW - sig.ALL_RED:.1f}",
                            sig.YELLOW, sig.ALL_RED, f"{ph.green:.1f}"])
                t += split
            w.writerow(["total", "", f"{t:.1f}"])
        index.append({"alt": tag, "cycle": plan.cycle, "treatment": treat,
                      "hcm_delay": res["intersection"]["d"],
                      "hcm_los": res["intersection"]["los"],
                      "file": os.path.basename(path)})

    json.dump(index, open(os.path.join(outdir, "alternatives.json"), "w"), indent=2)

    with open(os.path.join(outdir, "README.md"), "w") as fh:
        fh.write(f"""# Vissim model inputs

Site: {meta.get('location_name')}
Count: {meta.get('count_id')}, {meta.get('count_date')}, signal PX {meta.get('px')}
Period: {period.upper()} peak hour starting {hour[0].start:%H:%M}

## Files

`vehicle_inputs.csv` — peak hour volume for each of the twelve movements, with
the turning fraction within each approach. Enter the approach totals as vehicle
inputs and the fractions as static routing decisions.

`pedestrian_inputs.csv` — pedestrian crossings per hour on each leg.

`controller_A.csv` … `controller_D.csv` — fixed-time signal controller for each
timing alternative. Splits sum to the cycle. Effective green is the split minus
4 s of lost time, which is what the capacity calculation uses; the green
interval is what you enter in Vissim.

## Building the model

1. Geometry from the DXF in `output/`, imported as a background, or drawn from
   the same lane configuration in `config/`.
2. Vehicle inputs and routing from `vehicle_inputs.csv`.
3. Heavy vehicle share by movement is in the count data; the PM peak is about
   3 percent overall on this corridor.
4. Signal control: fixed time, one controller per alternative.
5. Evaluation: node evaluation with delay and queue length, ten runs with
   different random seeds, 900 s warm up discarded.

## Comparing back

Export the node evaluation as an .att file and run:

    python vissim.py compare vissim/node_results.att \\
        output/timing-alternatives.json --alt A

Expect differences. Microsimulation captures blocking, lane changing and
platoon arrivals that the HCM equations do not, so simulated delay usually
exceeds the analytical value where a movement is near capacity.
""")
    print(f"wrote {outdir}/ with {len(index)} controller sheets, "
          f"vehicle and pedestrian inputs")


# -------------------------------------------------------------- compare side
def parse_att(path: str) -> dict:
    """Read a Vissim .att export into {movement: {delay, queue}}.

    Vissim writes a header block of $ lines, then a semicolon separated table.
    Column names vary with the evaluation configured, so this looks for the
    movement identifier and any delay or queue column rather than fixed
    positions.
    """
    rows, header = [], None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            if line.startswith("$"):
                header = line.split(":", 1)[-1].split(";")
                continue
            if header:
                rows.append(dict(zip(header, line.split(";"))))
    if not rows:
        raise SystemExit("no data rows found in the .att file")

    def find(keys, *needles):
        for k in keys:
            kl = k.lower()
            if all(n in kl for n in needles):
                return k
        return None

    keys = list(rows[0].keys())
    k_mov = find(keys, "movement") or find(keys, "turn") or keys[0]
    k_del = find(keys, "vehdelay") or find(keys, "delay")
    k_que = find(keys, "qlen") or find(keys, "queue")

    out = {}
    for r in rows:
        label = normalize_movement(r.get(k_mov, ""))
        if not label:
            continue
        try:
            d = float(r.get(k_del, "") or "nan")
        except ValueError:
            d = float("nan")
        try:
            q = float(r.get(k_que, "") or "nan")
        except ValueError:
            q = float("nan")
        out[label] = {"delay": d, "queue": q}
    return out


def normalize_movement(raw: str) -> str:
    """Turn Vissim movement labels into NBL style keys."""
    s = raw.upper().replace("-", " ").replace("_", " ")
    m = re.search(r"\b(NB|SB|EB|WB|N|S|E|W)\b", s)
    if not m:
        return ""
    d = m.group(1)
    d = d if len(d) == 2 else d + "B"
    if re.search(r"\bLEFT|\bL\b", s):
        t = "L"
    elif re.search(r"\bRIGHT|\bR\b", s):
        t = "R"
    else:
        t = "T"
    return d + t


def compare(att_path: str, results_path: str, alt: str) -> None:
    sim = parse_att(att_path)
    alts = json.load(open(results_path))
    chosen = next((a for a in alts if a["label"].startswith(alt)), alts[0])
    hcm_res = chosen["results"]["movements"]

    print(f"{chosen['label']}   cycle {chosen['cycle']:.0f} s\n")
    print(f"{'MVMT':<7}{'v/c':>8}{'HCM d':>9}{'SIM d':>9}{'DIFF':>9}"
          f"{'SIM QUEUE':>11}")
    diffs = []
    for k in MOVEMENT_ORDER:
        if k not in hcm_res:
            continue
        h = hcm_res[k]["d"]
        s = sim.get(k, {}).get("delay", float("nan"))
        q = sim.get(k, {}).get("queue", float("nan"))
        if s == s:
            diffs.append(s - h)
            print(f"{k:<7}{hcm_res[k]['X']:>8.3f}{h:>9.1f}{s:>9.1f}"
                  f"{s-h:>+9.1f}{q:>11.1f}")
        else:
            print(f"{k:<7}{hcm_res[k]['X']:>8.3f}{h:>9.1f}{'—':>9}{'—':>9}{'—':>11}")
    if diffs:
        mean = sum(diffs) / len(diffs)
        print(f"\nmean difference {mean:+.1f} s across {len(diffs)} movements")
        print("Positive means the simulation is slower than the HCM estimate.")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="write model inputs and controller sheets")
    e.add_argument("count_file")
    e.add_argument("config")
    e.add_argument("--period", choices=("am", "pm", "all"), default="pm")
    e.add_argument("--out", default="vissim")

    c = sub.add_parser("compare", help="compare a Vissim node evaluation to the HCM run")
    c.add_argument("att_file")
    c.add_argument("results_json")
    c.add_argument("--alt", default="A")

    args = p.parse_args()
    if args.cmd == "export":
        export(args.count_file, args.config, args.period, args.out)
    else:
        compare(args.att_file, args.results_json, args.alt)


if __name__ == "__main__":
    main()
