"""Optional original print artwork supplied privately by the deployment."""
import os
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from PIL import Image

from .storage import ROOT


@lru_cache(maxsize=2)
def _load(path, stamp):
    # The original ten slot types and the winding are fixed symbolic drawings.
    # Decode only these small, known image sizes, then re-encode clean PNG bytes.
    names = {f'c{i}': {(233, 353), (234, 353)} for i in range(1, 11)}
    names.update(x1={(234, 353)}, paper={(297, 440)})
    result = {}
    with ZipFile(path) as archive:
        for key, sizes in names.items():
            try:
                info = archive.getinfo(key + '.png')
                if info.file_size > 512_000:
                    continue
                with Image.open(BytesIO(archive.read(info))) as image:
                    if image.format != 'PNG' or image.size not in sizes:
                        continue
                    data = BytesIO()
                    image.convert('RGB' if key == 'paper' else 'L').save(data, format='PNG')
                    result[key] = dict(png=data.getvalue(), width=image.width, height=image.height)
            except (KeyError, OSError, ValueError, BadZipFile, RuntimeError, Image.DecompressionBombError):
                continue
    return result


def drawings():
    data = Path(os.environ.get('DCSJ_DATA_DIR', ROOT / '.runtime'))
    path = data / 'classic-print.zip'
    try:
        stat = path.stat()
        if stat.st_size <= 4_000_000:
            return _load(path, (stat.st_mtime_ns, stat.st_size, stat.st_ino))
    except (OSError, BadZipFile):
        pass
    return {}


def slot_picture(parameters, art):
    from .slot_diagrams import slot_drawings
    try:
        code = float(parameters.get('zc'))
        if not code.is_integer() or not 1 <= code <= 10:
            return None
        picture = art.get(f'c{int(code)}')
        if not picture or float(parameters['bs2']) <= 0 or float(parameters['bc02']) <= 0:
            return None
        stator, rotor = slot_drawings(parameters)
        # Types 9 and 10 have original symbolic plates even though this
        # application has no verified parametric outline for them.
        if stator['supported'] and (rotor['supported'] or code in (9, 10)):
            return picture
    except (KeyError, ValueError, TypeError):
        pass
    return None
