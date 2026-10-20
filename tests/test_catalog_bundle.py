import gzip
import hashlib
import json

import pytest

from dcsj import catalog_bundle


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_bundle, "ROOT", tmp_path)
    monkeypatch.delenv("DCSJ_CATALOG_PATH", raising=False)
    monkeypatch.setenv("DCSJ_DATA_DIR", str(tmp_path / "cache"))
    (tmp_path / "data").mkdir()
    content = b"verified catalog fixture"
    (tmp_path / "data/catalog.sqlite.gz").write_bytes(gzip.compress(content))
    manifest = {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    (tmp_path / "data/catalog_bundle.json").write_text(json.dumps(manifest))
    catalog_bundle.catalog_path.cache_clear()
    yield tmp_path, content
    catalog_bundle.catalog_path.cache_clear()


def test_verified_atomic_bootstrap_and_cache_repair(bundle):
    root, content = bundle
    expanded = catalog_bundle.catalog_path()
    assert expanded.read_bytes() == content
    expanded.write_bytes(b"damaged")
    catalog_bundle.catalog_path.cache_clear()
    assert catalog_bundle.catalog_path().read_bytes() == content
    assert not list((root / "cache").glob("*.tmp"))


def test_corrupt_bundle_is_never_published(bundle):
    root, content = bundle
    (root / "data/catalog.sqlite.gz").write_bytes(gzip.compress(b"wrong contents"))
    with pytest.raises(RuntimeError, match="checksum"):
        catalog_bundle.catalog_path()
    assert not list((root / "cache").glob("*.sqlite"))
    assert not list((root / "cache").glob("*.tmp"))
