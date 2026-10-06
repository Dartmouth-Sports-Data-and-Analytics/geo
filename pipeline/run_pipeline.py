"""run_pipeline.py — runs discover -> pull -> geocode -> build_data_json in order, stopping if a step fails."""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

STEPS = {
    "discover": "discover_sports.py",
    "pull": "pull_rosters.py",
    "geocode": "geocode_rosters.py",
    "build_data_json": "build_data_json.py",
}


def run_step(name, extra_args=()):
    print(f"\n== {name} ==", flush=True)
    result = subprocess.run([sys.executable, os.path.join(HERE, STEPS[name]), *extra_args])
    if result.returncode != 0:
        print(f"\n{name} exited with status {result.returncode}; stopping.")
        sys.exit(result.returncode)


# Options that only some steps understand are passed to those steps alone.
def step_args(step, args):
    extra = []
    if step in ("discover", "pull"):
        if args.workers:
            extra += ["--workers", str(args.workers)]
        if args.verbose:
            extra.append("--verbose")
    if step == "pull" and args.refresh:
        extra += ["--refresh", args.refresh]
    if step == "pull" and args.retry_empty:
        extra.append("--retry-empty")
    return extra


def main():
    parser = argparse.ArgumentParser(description="Run discover, pull, geocode and build_data_json in sequence.")
    parser.add_argument("--only", choices=list(STEPS), default=None, help="Run just one step.")
    parser.add_argument("--recheck", metavar="SCHOOL,SPORT_PAGE", default=None,
                        help="Diagnose one combo with discover_sports.py --recheck (runs only that step).")
    parser.add_argument("--workers", type=int, default=None, help="Schools to process at once in discover and pull (default 8).")
    parser.add_argument("--refresh", metavar="SPORT_PAGE[,SPORT_PAGE...]|all", default=None,
                        help="Refetch every year for these sport pages instead of trusting what is on disk (pull step).")
    parser.add_argument("--retry-empty", action="store_true", help="Ask again for past seasons that had no roster last time (pull step).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print every skipped sport and fetched page in discover and pull.")
    args = parser.parse_args()

    if args.recheck:
        run_step("discover", ["--recheck", args.recheck])
        return
    for step in ([args.only] if args.only else STEPS):
        run_step(step, step_args(step, args))


if __name__ == "__main__":
    main()
