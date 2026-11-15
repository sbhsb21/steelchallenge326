"""Import the SteelChallenge Master RESULTS and ANALYSIS sheets."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook

import eventlog
import storage


def read_sheet(sheet):
    rows = sheet.values
    headers = next(rows)
    result = {}
    for values in rows:
        row = dict(zip(headers, values))
        run_id = row["Run_ID"]
        if run_id is None:
            continue
        if run_id in result:
            raise ValueError(f"{sheet.title}: duplicate Run_ID {run_id}")
        result[run_id] = row
    return result


def measured(row):
    chemistry = {k[6:]: v for k, v in row.items()
                 if k.startswith("Steel_") and k != "Steel_Grade" and not k.endswith(("_Min", "_Max"))}
    process = {k[5:]: v for k, v in row.items()
               if k.startswith("Slag_") and not k.endswith(("_Min", "_Max"))}
    process["basicity"] = process.pop("Basicity", None)
    process.update(time_min=row["Time_min"], temp_c=row["Tap_Temperature_C"],
                   tapping_mass_t=row["Tapping_Mass_t"], energy_kwh=row["Total_Energy_kWh"])
    return {"chemistry": chemistry, "process": process,
            "cost": {"total": row["Total_Cost"], "per_t": row["Cost_Per_Tonne"]}}


def import_master(path):
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        results = read_sheet(book["RESULTS"])
        analysis = read_sheet(book["ANALYSIS"])
    finally:
        book.close()
    if results.keys() != analysis.keys():
        raise ValueError("RESULTS and ANALYSIS Run_ID sets differ.")
    storage.initialize()
    imported = 0
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for key, row in results.items():
            raw = row["Event_Log_Raw_01"]
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"Run_ID {key}: missing log")
            source = f"SteelChallenge Master:{key}"
            if conn.execute("SELECT 1 FROM master_run WHERE source_key=? UNION ALL "
                            "SELECT 1 FROM master_alias WHERE source_key=?", (source, source)).fetchone():
                continue
            if duplicate := conn.execute(
                """SELECT m.run_id FROM master_run m JOIN run r ON r.id=m.run_id
                   WHERE r.raw_log=? AND json_extract(m.result,'$.Run_Date')=?
                   AND json_extract(m.result,'$.Status')=? AND json_extract(m.result,'$.Score')=?
                   AND json_extract(m.result,'$.Steel_Grade')=?
                   AND json_extract(m.result,'$.Tapping_Mass_t')=?
                   AND json_extract(m.result,'$.Tap_Temperature_C')=?""",
                (raw, row["Run_Date"], row["Status"], row["Score"], row["Steel_Grade"],
                 row["Tapping_Mass_t"], row["Tap_Temperature_C"]),
            ).fetchone():
                conn.execute("INSERT INTO master_alias VALUES (?,?)", (source, duplicate["run_id"]))
                continue
            digest = hashlib.sha256(raw.encode()).hexdigest()
            match = conn.execute(
                """SELECT r.id FROM run r LEFT JOIN master_run m ON m.run_id=r.id
                   WHERE r.digest=? AND m.run_id IS NULL""", (digest,),
            ).fetchone()
            if match:
                run_id = match["id"]
            else:
                if conn.execute("SELECT 1 FROM run WHERE digest=?", (digest,)).fetchone():
                    digest = hashlib.sha256(f"{source}\0{raw}".encode()).hexdigest()
                cursor = conn.execute(
                    "INSERT INTO run(digest,name,raw_log) VALUES (?,?,?)",
                    (digest, f"{row['Steel_Grade']} · Master {key}", raw),
                )
                run_id = cursor.lastrowid
            actual = measured(row)
            # Existing manually recorded values take precedence over this import.
            existing = json.loads(conn.execute("SELECT actual FROM run WHERE id=?", (run_id,)).fetchone()[0])
            for group, values in actual.items():
                values.update(existing.get(group, {}))
            existing.update(actual)
            conn.execute("UPDATE run SET actual=? WHERE id=?", (json.dumps(existing), run_id))
            conn.execute("DELETE FROM event WHERE run_id=?", (run_id,))
            conn.executemany(
                "INSERT INTO event VALUES (?,?,?,?,?,?)",
                [(run_id, e["line"], e["seconds"], e["kind"], json.dumps(e["payload"]), e["raw"])
                 for e in eventlog.parse(raw)],
            )
            conn.execute("INSERT INTO master_run VALUES (?,?,?,?,?)",
                         (run_id, source, path.name,
                          json.dumps({k: v for k, v in row.items()
                                      if k not in {"Event_Log_Raw_01", "User_ID", "Sender", "Sender_Email"}},
                                     default=str),
                          json.dumps(analysis[key], default=str)))
            imported += 1
    logs = Counter(r["Event_Log_Raw_01"] for r in results.values())
    return imported, len(results), sum(count - 1 for count in logs.values())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    args = parser.parse_args()
    added, total, shared = import_master(args.workbook)
    print(f"Imported {added}/{total} Master records; {shared} extra record(s) share log text.")
