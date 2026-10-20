"""Run with: python run.py [--port 8767] [--host 127.0.0.1]."""
import argparse

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DCSJ 中文电磁设计工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    print(f"工作台 http://{args.host}:{args.port}    API 文档 http://{args.host}:{args.port}/docs")
    uvicorn.run("dcsj.api:app", host=args.host, port=args.port)
