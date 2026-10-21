"""Install a private bundle locally, or fetch from the configured private GitHub repository."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dcsj import resources

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='导入私有资源，不覆盖用户型号、材料或设计记录')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--file', type=Path)
    source.add_argument('--github', action='store_true')
    args = parser.parse_args()
    current = resources.current()
    previous = current['package_id'] if current else None
    if args.github:
        result = resources.import_remote(previous)
    else:
        with resources.import_slot():
            staged = resources.install(args.file)
            result = resources.activate(staged['package_id'], previous)
    print(json.dumps({key: result[key] for key in ('package_id', 'version', 'counts', 'changed')}, ensure_ascii=False, indent=2))
