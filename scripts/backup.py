"""Back up or restore into a new database path; never overwrite a live database."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dcsj.backup import backup, restore
from dcsj.storage import database_path

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DCSJ 设计数据库一致性备份与恢复')
    parser.add_argument('action', choices=['create', 'restore'])
    parser.add_argument('destination', type=Path, help='尚不存在的目标数据库路径')
    parser.add_argument('--source', type=Path, default=None)
    parser.add_argument('--sha256', help='恢复时核对备份文件的 SHA-256')
    args = parser.parse_args()
    if args.action == 'restore' and args.source is None:
        parser.error('恢复时必须使用 --source 指定备份文件')
    source = args.source or database_path()
    result = restore(source, args.destination, args.sha256) if args.action == 'restore' else backup(source, args.destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))
