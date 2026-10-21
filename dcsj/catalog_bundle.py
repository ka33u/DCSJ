"""Atomic bootstrap of an optional private catalog or an empty public catalog."""
import gzip
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import tempfile
from functools import lru_cache
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def catalog_path():
    from .resources import current, asset
    override = os.getenv("DCSJ_CATALOG_PATH")
    if override:
        path = Path(override).resolve()
        if not path.is_file():
            raise RuntimeError("DCSJ_CATALOG_PATH does not point to a file")
        return path
    package = current()
    if package:
        return asset('catalog.sqlite', package)
    return _legacy_catalog_path()


@lru_cache(maxsize=1)
def _legacy_catalog_path():
    source = ROOT / "data/catalog.sqlite"
    if source.exists():
        return source
    directory = Path(os.getenv("DCSJ_DATA_DIR", str(ROOT / ".runtime")))
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / "data/catalog_bundle.json"
    if not manifest_path.is_file():
        target = directory / "empty-catalog.sqlite"
        if not target.is_file():
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".sqlite", delete=False) as output:
                temporary = Path(output.name)
            try:
                with closing(sqlite3.connect(temporary)) as db:
                    db.executescript((ROOT / "data/schema.sql").read_text())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return target
    manifest = json.loads(manifest_path.read_text())
    target = directory / ("catalog-" + manifest["sha256"][:16] + ".sqlite")
    if target.exists() and target.stat().st_size == manifest["size"] and digest(target) == manifest["sha256"]:
        return target
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix="catalog-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            checksum, size = hashlib.sha256(), 0
            with gzip.open(ROOT / "data/catalog.sqlite.gz", "rb") as compressed:
                for chunk in iter(lambda: compressed.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > manifest["size"]:
                        raise RuntimeError("Catalog bundle exceeds declared size")
                    checksum.update(chunk)
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != manifest["size"] or checksum.hexdigest() != manifest["sha256"]:
            raise RuntimeError("Catalog bundle checksum mismatch")
        os.replace(temporary, target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


catalog_path.cache_clear = _legacy_catalog_path.cache_clear
