import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("STEEL_DB", ROOT / "steelchallenge.sqlite3"))
COUNTS = {"raw": 10, "eaf": 16, "secondary": 21}


@contextmanager
def connect():
    with closing(sqlite3.connect(DB, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn


def initialize():
    with connect() as conn:
        conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        if conn.execute("PRAGMA user_version").fetchone()[0] >= 2:
            return
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if "details" not in {row["name"] for row in conn.execute("PRAGMA table_info(material)")}:
            # SQLite requires a table rebuild to allow the UI's negative scrap price.
            conn.execute("""CREATE TABLE material_new (
                slot TEXT PRIMARY KEY,
                stage TEXT NOT NULL CHECK(stage IN ('raw','eaf','secondary')),
                name TEXT, price_per_t REAL,
                density_t_m3 REAL CHECK(density_t_m3 > 0),
                composition TEXT NOT NULL DEFAULT '{}', source TEXT,
                details TEXT NOT NULL DEFAULT '{}'
            )""")
            conn.execute("""INSERT INTO material_new
                (slot,stage,name,price_per_t,density_t_m3,composition,source)
                SELECT slot,stage,name,price_per_t,density_t_m3,composition,source FROM material""")
            conn.execute("DROP TABLE material")
            conn.execute("ALTER TABLE material_new RENAME TO material")
        conn.executemany(
            "INSERT OR IGNORE INTO material (slot, stage) VALUES (?, ?)",
            [(f"{stage}-{i:02}", stage) for stage, count in COUNTS.items()
             for i in range(1, count + 1)],
        )
        if version < 2:
            seed_catalog(conn)
            conn.execute("PRAGMA user_version = 2")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise sqlite3.IntegrityError("Material catalog migration broke a database reference.")


def seed_catalog(conn):
    catalog = json.loads((ROOT / "eaf_catalog.json").read_text(encoding="utf-8"))
    for item in catalog:
        row = conn.execute(
            "SELECT * FROM material WHERE stage=? AND name=? COLLATE NOCASE ORDER BY slot",
            (item["stage"], item["name"]),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM material WHERE stage=? AND name IS NULL ORDER BY slot",
                (item["stage"],),
            ).fetchone()
        if row is None:
            prefix = item["stage"]
            slots = conn.execute("SELECT slot FROM material WHERE stage=?", (prefix,))
            index = max(int(r["slot"].rsplit("-", 1)[1]) for r in slots) + 1
            slot = f"{prefix}-{index:02}"
            conn.execute("INSERT INTO material(slot, stage) VALUES (?,?)", (slot, prefix))
        else:
            slot = row["slot"]
        composition = json.loads(row["composition"]) if row else {}
        composition.update({k: v for k, v in item["composition"].items()
                            if v is not None or k not in composition})
        details = json.loads(row["details"]) if row else {}
        details.update(item["details"])
        conn.execute(
            """UPDATE material SET name=?,price_per_t=?,
               density_t_m3=COALESCE(?,density_t_m3),composition=?,source=?,details=? WHERE slot=?""",
            (item["name"], item["price_per_t"], item["density_t_m3"], json.dumps(composition),
             item["source"], json.dumps(details, ensure_ascii=False), slot),
        )


def rows(sql, args=()):
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, args)]


def execute(sql, args):
    with connect() as conn:
        conn.execute(sql, args)


def import_run(name, raw, events, prediction):
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO run (digest,name,raw_log,prediction) VALUES (?,?,?,?) "
            "ON CONFLICT(digest) DO NOTHING",
            (digest, name, raw, json.dumps(prediction) if prediction is not None else None),
        )
        if cursor.rowcount == 0:
            return conn.execute("SELECT id FROM run WHERE digest=?", (digest,)).fetchone()[0], False
        run_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO event VALUES (?,?,?,?,?,?)",
            [(run_id, e["line"], e["seconds"], e["kind"], json.dumps(e["payload"]), e["raw"])
             for e in events],
        )
        return run_id, True


if __name__ == "__main__":
    initialize()
    print(DB)

