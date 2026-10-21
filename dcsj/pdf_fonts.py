"""Pinned, optional PDF font installation; report generation never uses the network."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
FONT_REVISION = 'f8d157532fbfaeda587e826d4cd5b21a49186f7c'
SOURCE_SHA256 = 'd68bafcb48a2707749396aa12bbbd833cb70401f3a9a689fd2902c7e0d295964'
LICENSE_SHA256 = '6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2'
BASE_URL = f'https://raw.githubusercontent.com/notofonts/noto-cjk/{FONT_REVISION}/Sans/'


def font_path():
    explicit = os.getenv('DCSJ_PDF_FONT')
    return Path(explicit) if explicit else Path(os.getenv('DCSJ_DATA_DIR', str(ROOT / '.runtime'))) / 'fonts/DCSJPdfSans.ttf'


def status():
    return {'available': font_path().is_file(), 'embedded': True,
            'setup': 'python scripts/install_pdf_font.py', 'source': 'Noto Sans SC / SIL OFL 1.1'}


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def verified_download(url, destination, expected_hash, max_bytes):
    destination = Path(destination)
    if destination.is_file() and digest(destination) == expected_hash:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.download', delete=False) as output:
            temporary = Path(output.name)
            size = 0
            with urlopen(url, timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise RuntimeError('字体下载超过预期大小')
                    output.write(chunk)
            output.flush(); os.fsync(output.fileno())
        if digest(temporary) != expected_hash:
            raise RuntimeError('字体依赖校验失败，未安装该文件')
        os.replace(temporary, destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def install(destination=None):
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    target = Path(destination) if destination else font_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = target.with_suffix('.json')
    if target.is_file() and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            manifest = {}
        if not isinstance(manifest, dict):
            manifest = {}
        if manifest.get('source_sha256') == SOURCE_SHA256 and manifest.get('sha256') == digest(target):
            return manifest
    source = target.parent / 'NotoSansSC-VF.ttf'
    verified_download(BASE_URL + 'Variable/TTF/Subset/NotoSansSC-VF.ttf', source, SOURCE_SHA256, 17773132)
    verified_download(BASE_URL + 'LICENSE', target.parent / 'OFL.txt', LICENSE_SHA256, 4301)
    with TTFont(source, recalcTimestamp=False) as font:
        instantiateVariableFont(font, {'wght': 400}, inplace=True)
        # Name this local static derivative separately from the upstream font.
        for record in font['name'].names:
            names = {1: 'DCSJ PDF Sans', 2: 'Regular', 4: 'DCSJ PDF Sans Regular', 6: 'DCSJPDFSans-Regular',
                     16: 'DCSJ PDF Sans', 17: 'Regular'}
            if record.nameID in names:
                record.string = names[record.nameID].encode(record.getEncoding())
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.ttf', delete=False) as output:
                temporary = Path(output.name)
            font.save(temporary)
            os.replace(temporary, target)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    manifest = {'source_revision': FONT_REVISION, 'source_sha256': SOURCE_SHA256,
                'sha256': digest(target), 'weight': 400, 'license': 'SIL OFL 1.1'}
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest
