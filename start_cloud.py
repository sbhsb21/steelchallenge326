"""Prepare persistent storage and start the authenticated cloud service."""
import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile


ROOT = Path(__file__).resolve().parent


def prepare_database(destination, seed):
    if not destination.parent.is_dir():
        raise RuntimeError("The persistent disk mount is missing.")
    if destination.exists() or not seed.is_file():
        return
    # Publish a complete copy only after SQLite finishes writing it.
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with closing(sqlite3.connect(seed.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def main():
    os.environ["STEEL_REQUIRE_LOGIN"] = "1"
    secret = Path("/etc/secrets/secrets.toml")
    if secret.is_file():
        target = ROOT / ".streamlit" / "secrets.toml"
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(secret, target)
        target.chmod(0o600)
    prepare_database(Path(os.environ["STEEL_DB"]).resolve(), ROOT / "steelchallenge.sqlite3")
    os.execv(sys.executable, [
        sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
        "--server.address", "0.0.0.0", "--server.port", os.environ["PORT"],
        "--server.headless", "true", "--browser.gatherUsageStats", "false",
    ])


if __name__ == "__main__":
    main()
