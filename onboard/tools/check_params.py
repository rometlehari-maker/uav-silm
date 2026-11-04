#!/usr/bin/env python3
"""Validate a .param file against the real ArduPlane parameter names.

Builds the complete list of parameter names for ArduPlane by walking the
documentation tags in an ArduPilot source checkout (the same @Param / @Group /
@Path tags ArduPilot's own metadata generator uses). It only READS the source as
text; nothing from the checkout is executed.

Then it checks every line of your .param file and reports:
  * names that don't exist in that firmware version (typos, renamed params,
    params that only exist on Copter, ...)
  * names that appear more than once
  * values outside the documented @Range (where one is declared)

Usage:
    git clone --depth 1 --branch ArduPlane-stable https://github.com/ArduPilot/ardupilot
    python3 tools/check_params.py --src ardupilot ardupilot/gps_denied_failsafe.param

Exit code 0 = all good, 1 = problems found.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

PARAM_RE = re.compile(r"//\s*@Param(?:\{([^}]*)\})?:\s*([A-Za-z0-9_]+)")
GROUP_RE = re.compile(r"//\s*@Group(?:\{([^}]*)\})?:\s*([A-Za-z0-9_]*)\s*\n\s*//\s*@Path(?:\{([^}]*)\})?:\s*([^\n]+)")
RANGE_RE = re.compile(r"//\s*@Range(?:\{([^}]*)\})?:\s*(-?[0-9.]+)\s+(-?[0-9.]+)")


def _for_vehicle(filt: str | None, vehicle: str) -> bool:
    if not filt:
        return True
    return vehicle in [f.strip() for f in filt.split(",")]


def collect(src: str, vehicle: str = "Plane") -> dict[str, tuple[float, float] | None]:
    """Return {param_name: (min, max) or None} for the vehicle."""
    names: dict[str, tuple[float, float] | None] = {}
    seen: set[tuple[str, str]] = set()
    root_file = os.path.join(src, "Ardu" + vehicle, "Parameters.cpp")
    if not os.path.exists(root_file):
        raise SystemExit(f"not an ArduPilot checkout (missing {root_file})")

    def walk(path: str, prefix: str) -> None:
        key = (os.path.normpath(path), prefix)
        if key in seen or not os.path.exists(path):
            return
        seen.add(key)
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()

        # Params: pair each @Param with the first @Range that follows it before
        # the next @Param.
        matches = list(PARAM_RE.finditer(text))
        for i, m in enumerate(matches):
            if not _for_vehicle(m.group(1), vehicle):
                continue
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[m.end():end]
            rng = None
            for r in RANGE_RE.finditer(block):
                if _for_vehicle(r.group(1), vehicle):
                    rng = (float(r.group(2)), float(r.group(3)))
                    break
            names[prefix + m.group(2)] = rng

        # Groups: recurse into each referenced file with the extended prefix.
        for g in GROUP_RE.finditer(text):
            if not _for_vehicle(g.group(1), vehicle) or not _for_vehicle(g.group(3), vehicle):
                continue
            for rel in g.group(4).split(","):
                rel = rel.strip()
                if rel:
                    walk(os.path.join(os.path.dirname(path), rel), prefix + g.group(2))

    walk(root_file, "")
    return names


def read_param_file(path: str) -> list[tuple[int, str, float]]:
    out = []
    with open(path) as fh:
        for ln, line in enumerate(fh, 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = re.split(r"[,\s]+", line)
            if len(parts) < 2:
                continue
            out.append((ln, parts[0], float(parts[1])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="ArduPilot source checkout")
    ap.add_argument("--vehicle", default="Plane")
    ap.add_argument("paramfile")
    args = ap.parse_args()

    known = collect(args.src, args.vehicle)
    print(f"{len(known)} {args.vehicle} parameters found in {args.src}")

    problems = 0
    seen: dict[str, int] = {}
    for ln, name, val in read_param_file(args.paramfile):
        if name in seen:
            print(f"  line {ln}: DUPLICATE {name} (first on line {seen[name]})")
            problems += 1
        seen.setdefault(name, ln)
        if name not in known:
            hint = [k for k in known if k.endswith(name.split("_", 1)[-1])][:3]
            extra = f"  (similar: {', '.join(hint)})" if hint else ""
            print(f"  line {ln}: UNKNOWN {name}{extra}")
            problems += 1
            continue
        rng = known[name]
        if rng and not (rng[0] <= val <= rng[1]):
            print(f"  line {ln}: OUT OF RANGE {name}={val:g} (documented {rng[0]:g}..{rng[1]:g})")
            problems += 1

    print("OK: every parameter exists and is in range" if problems == 0
          else f"{problems} problem(s) found")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
