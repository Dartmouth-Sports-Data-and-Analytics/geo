"""
run_pipeline.py — runs discover_sports.py, pull_rosters.py,
geocode_rosters.py, and build_data_json.py in sequence, each as its own
subprocess. Each of those four stays fully independent and runnable on its
own exactly as before (e.g. `python discover_sports.py --recheck
Harvard,rowing`) -- this script is purely a convenience wrapper for running
all four back to back, not a dependency any of them needs.

Default: runs all four, in order, stopping immediately if one exits with
a non-zero status, since each phase depends on the one(s) before it having
succeeded (pull needs discovery's availability cache; geocode needs pull's
roster .csv files to already be on disk; build_data_json needs geocode's
geo-rosters/*.csv files to already be on disk).

Usage:
    python run_pipeline.py                          # discover -> pull -> geocode -> build_data_json
    python run_pipeline.py --only pull               # just one phase
    python run_pipeline.py --workers 4                # fewer/more concurrent schools, passed to discover/pull
    python run_pipeline.py --recheck Harvard,rowing  # passed through to discover_sports.py
    python run_pipeline.py --refresh baseball,mens-rowing # passed through to pull_rosters.py
"""
import argparse
import os
import subprocess
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

STEPS = {
    "discover": "discover_sports.py",
    "pull": "pull_rosters.py",
    "geocode": "geocode_rosters.py",
    "build_data_json": "build_data_json.py",
}


def run_step(name, extra_args=None):
    script = os.path.join(_THIS_DIR, STEPS[name])
    cmd = [sys.executable, script] + (extra_args or [])
    print(f"\n{'=' * 70}\nRunning {name}: {' '.join(cmd)}\n{'=' * 70}\n")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\n{name} exited with status {result.returncode} -- stopping pipeline.")
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(
        description="Run discover_sports.py, pull_rosters.py, geocode_rosters.py, "
                    "and build_data_json.py in sequence."
    )
    parser.add_argument(
        "--only",
        choices=list(STEPS.keys()),
        default=None,
        help="Run only one step instead of the full discover -> pull -> geocode -> "
             "build_data_json chain.",
    )
    parser.add_argument(
        "--recheck",
        metavar="SCHOOL,SPORT_PAGE",
        default=None,
        help="Passed straight through to discover_sports.py --recheck; implies --only discover.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Passed straight through to discover_sports.py/pull_rosters.py's own --workers "
             "(default: 8, one per school, if not set).",
    )
    parser.add_argument(
        "--refresh",
        metavar="SPORT_PAGE[,SPORT_PAGE...]|all",
        default=None,
        help="Passed straight through to pull_rosters.py --refresh: refetch every year for these "
             "sport_pages instead of trusting what's already on disk.",
    )
    args = parser.parse_args()

    workers_args = ["--workers", str(args.workers)] if args.workers else []
    refresh_args = ["--refresh", args.refresh] if args.refresh else []

    if args.recheck:
        run_step("discover", ["--recheck", args.recheck])
    elif args.only:
        extra = workers_args if args.only in ("discover", "pull") else []
        if args.only == "pull":
            extra = extra + refresh_args
        run_step(args.only, extra)
    else:
        for step in ("discover", "pull", "geocode", "build_data_json"):
            extra = workers_args if step in ("discover", "pull") else []
            if step == "pull":
                extra = extra + refresh_args
            run_step(step, extra)


if __name__ == "__main__":
    main()
