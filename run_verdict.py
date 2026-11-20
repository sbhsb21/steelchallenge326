"""Read the result and per-run criteria saved with a simulation."""

import math
import re


PROCESS = {
    "time_min": ("시간", "분"),
    "temp_c": ("온도", "°C"),
    "tapping_mass_t": ("용강량", "t"),
    "co2_kg": ("CO₂", "kg"),
}
SLAG = {"basicity": "염기도", "FeO": "FeO", "MgO": "MgO"}
ALIASES = {"time": "time_min", "temperature": "temp_c", "solidification": "응고"}
LABELS = {"pass": "합격", "fail": "불합격", "unknown": "미확인"}


def saved_verdict(actual, master, pdf):
    for value in (actual.get("passed"), master.get("Status"), pdf.get("status")):
        if isinstance(value, (bool, int, float)) and value in (0, 1):
            return "pass" if value else "fail"
        if isinstance(value, str):
            verdict = {"0": "fail", "1": "pass", "successful": "pass",
                       "not successful": "fail", "pass": "pass", "fail": "fail"}
            if value.strip().casefold() in verdict:
                return verdict[value.strip().casefold()]
    return "unknown"


def numeric(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def run_checks(actual, master, pdf):
    chemistry = actual.get("chemistry", {})
    process = actual.get("process", {})
    workbook = actual.get("workbook_spec", {}).get("checks", {})
    limits = actual.get("limits", {})
    chemistry_bounds = dict(pdf.get("chemistry_limits", {}))
    process_bounds = dict(pdf.get("process_limits", {}))
    slag_bounds = dict(pdf.get("slag_limits", {}))
    for name, value in master.items():
        if not name.endswith(("_Min", "_Max")) or not name.startswith(("Steel_", "Slag_")):
            continue
        field = name.split("_", 1)[1].rsplit("_", 1)[0]
        key = "basicity" if field == "Basicity" else field
        target = chemistry_bounds if name.startswith("Steel_") else slag_bounds
        bounds = target.setdefault(key, [None, None])
        index = int(name.endswith("_Max"))
        if numeric(value) is not None:
            target[key] = [value, bounds[1]] if index == 0 else [bounds[0], value]
    for key, item in workbook.items():
        target = chemistry_bounds if re.fullmatch(r"[A-Z][a-z]?", key) else slag_bounds if key in SLAG else process_bounds
        target[key] = [item.get("min"), item.get("max")]
    chemistry_bounds.update(limits.get("chemistry", {}))
    process_bounds.update(limits.get("process", {}))
    slag_bounds.update(limits.get("slag", {}))
    if (target := numeric(process.get("target_time_min"))) is not None and (
        margin := numeric(process.get("time_tolerance_min"))
    ) is not None:
        process_bounds.setdefault("time_min", [max(0, target - margin), target + margin])

    failed = {ALIASES.get(key, key) for key in actual.get("failed_constraints", [])}
    checks = []
    groups = (
        ("조성", chemistry, chemistry_bounds,
         sorted(chemistry.keys() | chemistry_bounds.keys() | {key for key in failed if re.fullmatch(r"[A-Z][a-z]?", key)})),
        ("공정", process, process_bounds, list(dict.fromkeys([*PROCESS, *process_bounds]))),
        ("슬래그", {**actual.get("slag", {}), **process}, slag_bounds,
         [key for key in dict.fromkeys([*SLAG, *slag_bounds])
          if key in slag_bounds or key in process or key in actual.get("slag", {})]),
    )
    for group, measured, bounds, keys in groups:
        for key in keys:
            name, unit = PROCESS.get(key, (SLAG.get(key, key), "wt%" if group != "공정" and key != "basicity" else ""))
            value = numeric(measured.get(key))
            lo, hi = map(numeric, bounds.get(key, [None, None]))
            if key in failed:
                verdict = "불합격 · 기록"
            elif value is None:
                verdict = "실측 미기록"
            elif lo is None and hi is None:
                verdict = "기준 미기록"
            elif (lo is not None and value < lo) or (hi is not None and value > hi):
                verdict = "불합격 · 기준 미달" if lo is not None and value < lo else "불합격 · 기준 초과"
            else:
                verdict = "합격"
            checks.append(dict(key=key, group=group, name=name, unit=unit, value=value,
                               min=lo, max=hi, verdict=verdict))
    return checks, sorted(failed - {row["key"] for row in checks})
