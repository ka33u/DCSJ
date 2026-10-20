"""Consistent SQLite backup with integrity verification; never overwrites a file."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise FileNotFoundError('设计数据库不存在')
    if destination.exists():
        raise FileExistsError('备份目标已经存在，请使用新的文件名')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='backup-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as incoming:
            with closing(sqlite3.connect(temporary)) as outgoing:
                incoming.backup(outgoing, pages=256, sleep=.05)
                result = outgoing.execute('PRAGMA quick_check').fetchone()[0]
                if result != 'ok':
                    raise RuntimeError('备份完整性检查失败')
                outgoing.commit()
        # Hard link creation is atomic and fails if another process created the target.
        os.link(temporary, destination)
        info = {'file': destination.name, 'bytes': destination.stat().st_size,
                'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(), 'quick_check': 'ok'}
        return info
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def restore(source, destination, expected_sha256=None):
    """Restore to a NEW path only; deployment switches paths after stopping the service."""
    source = Path(source)
    if expected_sha256 and hashlib.sha256(source.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError('备份文件校验和不匹配')
    return backup(source, destination)
