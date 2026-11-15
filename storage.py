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


# ----------------------------------------
# Supabase PostgreSQL 연결
# ----------------------------------------
def enable_supabase():
    import psycopg
    import streamlit as st
    from psycopg.rows import dict_row

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        database_url = st.secrets.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL이 설정되지 않았습니다.")

    def pg_connect():
        return psycopg.connect(
            database_url,
            row_factory=dict_row,
            connect_timeout=30,
            sslmode="require",
        )

    def pg_sql(query):
        # 기존 SQLite 쿼리를 PostgreSQL 문법으로 변환
        query = query.replace("?", "%s")
        query = query.replace(
            "json_extract(p.details,'$.process_type')",
            "(p.details::jsonb ->> 'process_type')",
        )
        return query

    def pg_initialize():
        # 테이블과 데이터는 이미 Supabase에 이전 완료
        with pg_connect() as conn:
            conn.execute("SELECT 1 FROM material LIMIT 1")

    def pg_rows(query, args=()):
        with pg_connect() as conn:
            return conn.execute(
                pg_sql(query), args
            ).fetchall()

    def pg_execute(query, args=()):
        with pg_connect() as conn:
            conn.execute(pg_sql(query), args)

    def pg_import_run(name, raw, events, prediction):
        digest = hashlib.sha256(raw.encode()).hexdigest()

        with pg_connect() as conn:
            result = conn.execute(
                """
                INSERT INTO run
                    (digest, name, raw_log, prediction)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(digest) DO NOTHING
                RETURNING id
                """,
                (
                    digest,
                    name,
                    raw,
                    json.dumps(prediction)
                    if prediction is not None else None,
                ),
            ).fetchone()

            if result is None:
                existing = conn.execute(
                    "SELECT id FROM run WHERE digest = %s",
                    (digest,),
                ).fetchone()
                return existing["id"], False

            run_id = result["id"]

            values = [
                (
                    run_id,
                    e["line"],
                    e["seconds"],
                    e["kind"],
                    json.dumps(e["payload"]),
                    e["raw"],
                )
                for e in events
            ]

            if values:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO event
                        (run_id, line, seconds, kind, payload, raw)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        values,
                    )

            return run_id, True

    globals()["initialize"] = pg_initialize
    globals()["rows"] = pg_rows
    globals()["execute"] = pg_execute
    globals()["import_run"] = pg_import_run


# DATABASE_URL이 있으면 Supabase 사용
import streamlit as st

if os.environ.get("DATABASE_URL") or st.secrets.get("DATABASE_URL"):
    enable_supabase()
