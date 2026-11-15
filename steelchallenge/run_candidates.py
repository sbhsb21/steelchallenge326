"""Rank replayable recipes against the requested grade using measured results."""

import json
import re

import eventlog


def recipe(raw):
    baskets, additions, steps = [], [], []
    for event in eventlog.parse(raw):
        seconds = event["seconds"]
        message = event["payload"]["message"]
        if seconds is None or event["kind"] in {"settings", "simulation_rate"}:
            continue
        minutes, sec = divmod(int(seconds), 60)
        hours, minute = divmod(minutes, 60)
        stamp = f"{hours:02}:{minute:02}:{sec:02}"
        if match := re.search(r"Scrap basket added with:\s*(.+)", message, re.I):
            baskets.append({"시각": stamp, "Basket": len(baskets) + 1, "장입 순서 및 중량": match[1]})
        if match := re.search(r"\bAdditions:\s*(.+)|\b(\d+(?:\.\d+)?\s*kg\s*:\s*.+)", message, re.I):
            additions.append({"시각": stamp, "첨가물 및 투입량": match[1] or match[2]})
        steps.append(f"{stamp}  {message}")
    return baskets, additions, "\n".join(steps)


def rank_runs(runs, target):
    ranked = []
    for run in runs:
        actual = json.loads(run["actual"])
        baskets, additions, timeline = recipe(run["raw_log"])
        if not timeline or (run["process_type"] == "EAF" and not baskets):
            continue
        checks, distance, failed, unknown = [], 0.0, 0, 0
        for group in ("chemistry", "process"):
            for name, (lo, hi) in target[group].items():
                if lo is None and hi is None:
                    continue
                value = actual.get(group, {}).get(name)
                verdict = "충족"
                if value is None:
                    unknown += 1
                    verdict = "미확인"
                else:
                    gap = max(lo - value if lo is not None else 0,
                              value - hi if hi is not None else 0, 0)
                    if gap:
                        failed += 1
                        verdict = "범위 밖"
                        scale = (hi - lo) if lo is not None and hi is not None else abs(hi if hi is not None else lo)
                        distance += gap / max(scale, 1e-6)
                checks.append({"항목": name, "Min": lo, "Max": hi, "기존 Run 실측": value, "판정": verdict})
        cost = actual.get("cost", {}).get("per_t")
        ranked.append(dict(run, actual=actual, baskets=baskets, additions=additions, timeline=timeline,
                           checks=checks, failed=failed, unknown=unknown, distance=distance, cost=cost))
    return sorted(ranked, key=lambda r: (r["failed"], r["unknown"], r["distance"],
                                         r["cost"] if r["cost"] is not None else float("inf")))
