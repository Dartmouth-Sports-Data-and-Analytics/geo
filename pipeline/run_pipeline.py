"""run_pipeline.py — runs discover -> pull -> infer -> geocode -> build_data_json in order, stopping if a step fails.

Before anything runs it checks that the packages are installed and that the scripts themselves are consistent (see find_code_problems),
so a half-copied update or a typo is reported in a second instead of twenty minutes into a pull."""
import argparse
import ast
import builtins
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# step -> script, plus the subcommand for the three that live in rosters.py
STEPS = {
    "discover": ("rosters.py", "discover"),
    "pull": ("rosters.py", "pull"),
    "infer": ("rosters.py", "infer"),
    "geocode": ("geocode_rosters.py",),
    "build_data_json": ("build_data_json.py",),
}


# import name -> pip name
REQUIRED_PACKAGES = {"pandas": "pandas", "requests": "requests", "bs4": "beautifulsoup4", "geopy": "geopy", "openpyxl": "openpyxl"}


def missing_packages():
    found = []
    for module, pip_name in REQUIRED_PACKAGES.items():
        try:
            present = module in sys.modules or importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            present = module in sys.modules
        if not present:
            found.append(pip_name)
    return found


def _top_level_names(tree):
    """(imported, defined) names at the top of a module; a name in both means the definition silently replaces the import."""
    imported, defined, order = set(), [], []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {a.asname or a.name for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defined.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                defined += [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]
    return imported, defined


def find_code_problems(folder=HERE):
    """Static checks of the pipeline's own files. Finds: a file that does not parse; a name imported and then defined again; a function
    defined twice (the second silently wins); `module.name` and `from module import name` that point at something that does not exist
    (a script copied from a different version); a name used but never defined; an import nothing uses."""
    problems, trees = [], {}
    for f in sorted(os.listdir(folder)):
        if f.endswith(".py"):
            try:
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    trees[f[:-3]] = ast.parse(fh.read(), f)
            except SyntaxError as e:
                problems.append(f"{f}: does not parse (line {e.lineno}: {e.msg})")
    exports = {}
    for name, tree in trees.items():
        imported, defined = _top_level_names(tree)
        exports[name] = imported | set(defined)
    if "config" in trees:       # config provides its rule tables on first use, so they are not defined at the top
        for node in trees["config"].body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_RULE_TABLE_NAMES" for t in node.targets):
                exports["config"] |= {c.value for c in ast.walk(node.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)}

    for name, tree in sorted(trees.items()):
        f = name + ".py"
        imported, defined = _top_level_names(tree)
        for n in sorted(imported & set(defined)):
            problems.append(f"{f}: imports {n} and also defines it, so the definition wins and the import is ignored")
        funcs = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
        for n in sorted({x for x in funcs if funcs.count(x) > 1}):
            problems.append(f"{f}: defines {n} more than once, so only the last one counts")
        local = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names if a.name in trees}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in local and node.value.id != name:
                if node.attr not in exports[node.value.id]:
                    problems.append(f"{f}: uses {node.value.id}.{node.attr}, which {node.value.id}.py does not define (mixed-up versions?)")
            elif isinstance(node, ast.ImportFrom) and node.module in trees and node.module != name:
                for a in node.names:
                    if a.name not in exports[node.module]:
                        problems.append(f"{f}: imports {a.name} from {node.module}.py, which does not define it (mixed-up versions?)")
        # names that are used but never defined anywhere in the file
        known = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                known.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.Lambda)):
                args = node.args
                known |= {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
                known |= {a.arg for a in (args.vararg, args.kwarg) if a}
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                known.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                known.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                known |= {(a.asname or a.name).split(".")[0] for a in node.names}
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                known |= set(node.names)
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        for n in sorted(used - known):
            problems.append(f"{f}: uses {n}, which is not defined anywhere in the file")
        for n in sorted(imported - used - {x.attr for x in ast.walk(tree) if isinstance(x, ast.Attribute)} - {"annotations"}):
            if n not in used:
                problems.append(f"{f}: imports {n} but never uses it")
    return problems


def preflight():
    problems = find_code_problems()
    missing = missing_packages()
    if missing:
        problems.insert(0, f"Missing Python packages: {', '.join(missing)}. Run: pip install -r requirements.txt")
    if problems:
        print("Stopping before anything runs; fix these first (--skip-check to run anyway):\n  " + "\n  ".join(problems))
        sys.exit(1)


def run_step(name, extra_args=()):
    print(f"\n== {name} ==", flush=True)
    script, *command = STEPS[name]
    result = subprocess.run([sys.executable, os.path.join(HERE, script), *command, *extra_args])
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
    if step == "geocode" and args.retry_failed:
        extra.append("--retry-failed")
    return extra


def main():
    parser = argparse.ArgumentParser(description="Run discover, pull, infer, geocode and build_data_json in sequence.")
    parser.add_argument("--only", choices=list(STEPS), default=None, help="Run just one step.")
    parser.add_argument("--recheck", metavar="SCHOOL,SPORT_PAGE", default=None,
                        help="Diagnose one combo with `rosters.py discover --recheck` (runs only that step).")
    parser.add_argument("--workers", type=int, default=None, help="Schools to process at once in discover and pull (default 8).")
    parser.add_argument("--refresh", metavar="SPORT_PAGE[,SPORT_PAGE...]|all", default=None,
                        help="Refetch every year for these sport pages instead of trusting what is on disk (pull step).")
    parser.add_argument("--retry-empty", action="store_true", help="Ask again for past seasons that had no roster last time (pull step).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print every skipped sport and fetched page in discover and pull.")
    parser.add_argument("--retry-failed", action="store_true", help="Look up again the hometowns on the geocoder's skip list, e.g. after adding fixes for them (geocode step).")
    parser.add_argument("--skip-check", action="store_true", help="Skip the check of the packages and scripts that runs first.")
    args = parser.parse_args()

    if not args.skip_check:
        preflight()
    if args.recheck:
        run_step("discover", ["--recheck", args.recheck])
        return
    for step in ([args.only] if args.only else STEPS):
        run_step(step, step_args(step, args))


if __name__ == "__main__":
    main()
