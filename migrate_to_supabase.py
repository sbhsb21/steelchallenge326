
import sqlite3
from getpass import getpass

import psycopg
from psycopg import sql

SOURCE = "steelchallenge.sqlite3"

TABLES = [
    "material",
    "grade",
    "route",
    "run",
    "recovery",
    "melt_sample",
    "event",
    "master_run",
    "pdf_source",
    "master_alias",
    "pdf_review",
]

uri = getpass("Supabase Session pooler URI: ")

try:
    with sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row

        with psycopg.connect(
            uri, connect_timeout=30, sslmode="require"
        ) as target:

            # 기존 Supabase 데이터가 있으면 중단
            for table in TABLES:
                count = target.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(
                        sql.Identifier(table)
                    )
                ).fetchone()[0]

                if count != 0:
                    raise RuntimeError(
                        f"{table}: Supabase에 이미 {count}건 존재. 이전 중단."
                    )

            # 기존 데이터를 외래키 의존 순서에 맞춰 이전
            for table in TABLES:
                records = source.execute(
                    f'SELECT * FROM "{table}"'
                ).fetchall()

                if records:
                    columns = records[0].keys()
                    statement = sql.SQL(
                        "INSERT INTO {} ({}) VALUES ({})"
                    ).format(
                        sql.Identifier(table),
                        sql.SQL(", ").join(
                            sql.Identifier(c) for c in columns
                        ),
                        sql.SQL(", ").join(
                            sql.Placeholder() for _ in columns
                        ),
                    )

                    with target.cursor() as cursor:
                         cursor.executemany(
                            statement,
                            [tuple(row) for row in records],
                        )

                actual = target.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(
                        sql.Identifier(table)
                    )
                ).fetchone()[0]

                if actual != len(records):
                    raise RuntimeError(
                        f"{table}: {len(records)}건 중 {actual}건 이전"
                    )

                print(f"{table}: {actual} records OK")

            # 명시적으로 이전한 ID 다음부터 자동 발급
            for table in ("run", "melt_sample"):
                target.execute(
                    """
                    SELECT setval(
                        pg_get_serial_sequence(%s, 'id'),
                        GREATEST(
                            COALESCE(
                                (SELECT MAX(id) FROM public.run),
                                0
                            ),
                            1
                        ),
                        EXISTS(SELECT 1 FROM public.run)
                    )
                    """
                    if table == "run" else
                    """
                    SELECT setval(
                        pg_get_serial_sequence(%s, 'id'),
                        GREATEST(
                            COALESCE(
                                (SELECT MAX(id) FROM public.melt_sample),
                                0
                            ),
                            1
                        ),
                        EXISTS(SELECT 1 FROM public.melt_sample)
                    )
                    """,
                    (f"public.{table}",),
                )

    print("Migration completed successfully!")

except Exception as error:
    print("Migration failed:", type(error).__name__)
    print(str(error))
    print("Check the error before trying again.")
