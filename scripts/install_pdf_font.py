"""Install the pinned, licensed Chinese PDF font locally. No design data is sent."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dcsj.pdf_fonts import install

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='安装 PDF 报告中文字体（下载官方 Noto 字体并校验）')
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(install(args.destination), ensure_ascii=False, indent=2))
