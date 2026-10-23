"""Build a private, deterministic resource package; never publish it to the source repository."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dcsj.resources import ABI, FILES, TX_FILES, ROOT, digest, validate_files


def build(source_root, destination, version):
    source_root, destination = Path(source_root), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    staging = destination / 'files'
    staging.mkdir(exist_ok=True)
    tx_present = [(source_root / source).is_file() for source in TX_FILES.values()]
    if any(tx_present) and not all(tx_present):
        raise ValueError('TX 型号库和计算资源须一起提供')
    files = {**FILES, **TX_FILES} if all(tx_present) else FILES
    for name, source in files.items():
        shutil.copyfile(source_root / source, staging / name)
    with closing(sqlite3.connect((staging / 'catalog.sqlite').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        counts = {'model_versions': db.execute('SELECT count(*) FROM models').fetchone()[0],
                  'models': db.execute('SELECT count(*) FROM models WHERE is_primary=1').fetchone()[0],
                  'materials': db.execute('SELECT count(*) FROM materials WHERE is_primary=1').fetchone()[0]}
    if all(tx_present):
        counts['tx_model_versions'] = len(json.loads((staging / 'tx_catalog.json').read_text())['models'])
    manifest = {'format': 'dcsj-private-resources', 'format_version': 2 if all(tx_present) else 1, 'abi': ABI,
                'version': version, 'minimum_application': '0.8.0' if all(tx_present) else '0.6.0', 'counts': counts,
                'files': {name: {'size': (staging / name).stat().st_size, 'sha256': digest(staging / name)} for name in files}}
    validate_files(staging, manifest)
    (staging / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding='utf-8')
    archive = destination / 'resource-bundle.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted([*files, 'manifest.json']):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with (staging / name).open('rb') as source, bundle.open(info, 'w') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
    release = {**manifest, 'archive': archive.name, 'sha256': digest(archive), 'size': archive.stat().st_size}
    parts = []
    with archive.open('rb') as source:
        while chunk := source.read(3 * 1024 * 1024):
            part = destination / f'{archive.name}.part{len(parts) + 1:03}'
            part.write_bytes(chunk)
            parts.append({'name': part.name, 'size': len(chunk), 'sha256': digest(part)})
    release['parts'] = parts
    (destination / 'manifest.json').write_text(json.dumps(release, ensure_ascii=False, indent=2), encoding='utf-8')
    return release


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='在私有目录构建资源包，不进行网络上传')
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    result = build(args.source_root, args.output, args.version)
    print(json.dumps({key: result[key] for key in ('version', 'sha256', 'size', 'counts')}, ensure_ascii=False, indent=2))
