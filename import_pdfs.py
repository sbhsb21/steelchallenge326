"""Import text-layer SteelChallenge PDF reports and record OCR review files."""
import argparse
import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader

import eventlog
import storage

NUM = r"-?\d+(?:\.\d+)?"
HEADER = re.compile(r"(?m)^(Electric Arc Furnace|Secondary Steelmaking)\s*\nComments\s*\nRun Information")


def field(text, name):
    match = re.search(rf"(?m)^{re.escape(name)} +([^\n]+)", text)
    return match[1].strip() if match else None


def number(text, name):
    match = re.match(rf"\$?\s*({NUM})", field(text, name) or "")
    return float(match[1]) if match else None


def report_rows(text, start, stop):
    match = re.search(rf"(?ms)^{start}[^\n]*\n(.*?)(?=^(?:{stop})\b|\Z)", text)
    return match[1].splitlines() if match else []


def table(text, start, stop):
    values, limits = {}, {}
    for line in report_rows(text, start, stop):
        match = re.fullmatch(rf"\s*([A-Za-z][A-Za-z0-9]*) +({NUM})(?: +({NUM}) +({NUM}))?\s*", line)
        if match:
            key, value, lo, hi = match.groups()
            key = "basicity" if key == "Basicity" else key
            values[key] = float(value)
            if lo is not None:
                limits[key] = [float(lo), float(hi)]
    return values, limits


def extract_report(text, process):
    match = re.search(r'(?ms)^Event Log\s*\n\s*\[(.*?)^\s*\]', text)
    if not match:
        return None
    logs = re.findall(r'"([^"]*)"', match[1])
    if not logs:
        return None
    raw = "\n".join(''.join(line.strip() for line in item.splitlines()) for item in logs)
    events = eventlog.parse(raw)
    if not events or not all(e["seconds"] is not None for e in events):
        return None
    chemistry, limits = table(text, "Steel Composition", "Raw Materials|Additions|Slag|Event Log")
    slag, slag_limits = table(text, "Slag Composition", "Event Log")
    mass = number(text, "Tapping mass")
    secondary = process == "Secondary Steelmaking"
    metrics = dict(slag, time_min=number(text, "Time (in minutes)"),
                   temp_c=number(text, "Tap temperature"),
                   tapping_mass_t=mass / 1000 if secondary and mass is not None else mass)
    if secondary:
        initial = number(text, "Initial mass")
        metrics["initial_mass_t"] = initial / 1000 if initial is not None else None
    else:
        metrics["energy_kwh"] = number(text, "Total Energy")
    cost = {"total": number(text, "Total Cost"), "per_t": number(text, "Cost Per Tonne")}
    def quantities(start, stop):
        rows = report_rows(text, start, stop)
        return {m[1]: float(m[2]) for line in rows
                if (m := re.fullmatch(rf"(.+?) +({NUM})\s*", line))}

    info = {"process_type": process, "date": field(text, "Date"),
            "grade": field(text, "Steel Grade"), "status": number(text, "Status"),
            "chemistry_limits": limits, "slag_limits": slag_limits,
            "raw_materials_t": quantities(r"Raw Materials\s*", "Additions|Slag|Event Log"),
            "eaf_additions_kg": quantities(r"Additions[ ]*$", "Slag|Event Log") if not secondary else {}}
    return raw, events, {"chemistry": chemistry, "process": metrics, "cost": cost}, info


def signature(events):
    return tuple((e["seconds"], " ".join(e["payload"]["message"].split())) for e in events)


def close_result(old, new):
    for group, key in (("cost", "per_t"), ("process", "temp_c"), ("process", "tapping_mass_t")):
        a, b = old.get(group, {}).get(key), new[group].get(key)
        if a is not None and b is not None and abs(a-b) > 0.011:
            return False
    return True


def merge_identical_master_runs(conn):
    seen = {}
    for row in conn.execute(
        """SELECT r.*,m.source_key,m.result FROM run r
           JOIN master_run m ON m.run_id=r.id ORDER BY r.id"""
    ).fetchall():
        result = json.loads(row["result"])
        for key in ("Run_ID", "Source_Run_Index"):
            result.pop(key, None)
        key = (row["raw_log"], json.dumps(result, sort_keys=True), row["actual"], row["prediction"])
        if key in seen:
            keep = seen[key]
            conn.execute("UPDATE pdf_source SET run_id=? WHERE run_id=?", (keep, row["id"]))
            conn.execute("UPDATE master_alias SET run_id=? WHERE run_id=?", (keep, row["id"]))
            conn.execute("INSERT INTO master_alias VALUES (?,?)", (row["source_key"], keep))
            conn.execute("DELETE FROM run WHERE id=?", (row["id"],))
        else:
            seen[key] = row["id"]


def run_import(root, ocr):
    storage.initialize()
    paths = sorted(p for p in root.rglob("*.pdf") if re.match(
        r"^(?:Gmail - )?(?:Electric Arc Furnace simulation|Secondary Steelmaking)", p.name))
    added = linked = held = 0
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        merge_identical_master_runs(conn)
        existing = {}
        for row in conn.execute("SELECT id,raw_log,actual FROM run"):
            existing.setdefault(signature(eventlog.parse(row["raw_log"])), []).append(
                (row["id"], json.loads(row["actual"])))
        for path in paths:
            relative = path.relative_to(root).as_posix()
            if re.fullmatch(r"Electric Arc Furnace simulation(?:_[2-6])?\.pdf", path.name):
                candidate = ocr / (path.stem + " - OCR.txt") if ocr else None
                if candidate and candidate.exists():
                    conn.execute("INSERT OR REPLACE INTO pdf_review VALUES (?,?,?)",
                                 (relative, "OCR 원본 대조 필요", candidate.read_text(encoding="utf-8-sig")))
                    held += 1
                    continue
            pages = ["\n".join(line for line in (p.extract_text() or "").splitlines()
                               if not re.search(r"mail\.google\.com|오[전후].*Gmail", line))
                     for p in PdfReader(path).pages]
            text = "\n".join(pages)
            headers = list(HEADER.finditer(text))
            for i, head in enumerate(headers):
                source = f"{relative} · {i + 1}"
                if conn.execute("SELECT 1 FROM pdf_source WHERE source=?", (source,)).fetchone():
                    continue
                section = text[head.end():headers[i+1].start() if i+1 < len(headers) else len(text)]
                parsed = extract_report(section, head[1])
                if parsed is None:
                    conn.execute("INSERT OR REPLACE INTO pdf_review VALUES (?,?,?)",
                                 (source, "로그 형식 확인 필요", section))
                    held += 1
                    continue
                raw, events, actual, info = parsed
                key = signature(events)
                duplicate = next((rid for rid, old in existing.get(key, []) if close_result(old, actual)), None)
                if duplicate is None:
                    digest = hashlib.sha256(raw.encode()).hexdigest()
                    if conn.execute("SELECT 1 FROM run WHERE digest=?", (digest,)).fetchone():
                        digest = hashlib.sha256(f"{source}\0{raw}".encode()).hexdigest()
                    cursor = conn.execute("INSERT INTO run(digest,name,raw_log,actual) VALUES (?,?,?,?)",
                                          (digest, f"{info['grade']} · {head[1]} · {info['date']}",
                                           raw, json.dumps(actual)))
                    duplicate = cursor.lastrowid
                    conn.executemany("INSERT INTO event VALUES (?,?,?,?,?,?)",
                                     [(duplicate, e["line"], e["seconds"], e["kind"],
                                       json.dumps(e["payload"]), e["raw"]) for e in events])
                    existing.setdefault(key, []).append((duplicate, actual))
                    added += 1
                else:
                    linked += 1
                conn.execute("INSERT INTO pdf_source VALUES (?,?,?)", (source, duplicate, json.dumps(info)))
    return added, linked, held


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--ocr", type=Path)
    args = parser.parse_args()
    print("New, linked, review:", run_import(args.root, args.ocr))
