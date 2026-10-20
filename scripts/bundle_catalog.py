"""Create a deterministic Git-friendly catalog bundle from the migrated database."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "data/catalog.sqlite"
target = ROOT / "data/catalog.sqlite.gz"
with source.open("rb") as incoming, target.open("wb") as outgoing:
    with gzip.GzipFile(filename="", mode="wb", fileobj=outgoing, mtime=0, compresslevel=6) as compressed:
        shutil.copyfileobj(incoming, compressed)
manifest = {"format_version": 1, "size": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "compressed_size": target.stat().st_size,
            "compressed_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
(ROOT / "data/catalog_bundle.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps(manifest))
