#!/usr/bin/env python3
"""
ArtifactFlow API 服务器启动脚本

使用方式:
    python run_server.py
    python run_server.py --host 0.0.0.0 --port 8000
    python run_server.py --reload  # 开发模式
"""

import argparse
import sys
from pathlib import Path

# 将 src 目录添加到 Python 路径
SRC_DIR = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_DIR))

import uvicorn
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="ArtifactFlow API Server")
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to (default: 8000)"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (default: 1)"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Log level (default: info)"
    )

    args = parser.parse_args()

    # 构造友好的显示 URL
    # 如果绑定 0.0.0.0，显示 localhost 方便点击，否则显示实际 host
    display_host = "localhost" if args.host == "0.0.0.0" else args.host
    docs_url = f"http://{display_host}:{args.port}/docs"
    redoc_url = f"http://{display_host}:{args.port}/redoc"

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                    ArtifactFlow API Server                   ║
╠══════════════════════════════════════════════════════════════╣
║  Host: {args.host:<53} ║
║  Port: {args.port:<53} ║
║  Workers: {args.workers:<50} ║
║  Reload: {str(args.reload):<51} ║
║  Log Level: {args.log_level:<48} ║
╠══════════════════════════════════════════════════════════════╣
║  Swagger UI: {docs_url:<47} ║
║  ReDoc:      {redoc_url:<47} ║
╚══════════════════════════════════════════════════════════════╝
""")

    # 配置 uvicorn
    uvicorn_kwargs = {
        "host": args.host,
        "port": args.port,
        "log_level": args.log_level,
    }

    if args.reload:
        uvicorn_kwargs["reload"] = True
        uvicorn_kwargs["reload_dirs"] = [str(SRC_DIR)]
    else:
        uvicorn_kwargs["workers"] = args.workers

    uvicorn.run("api.main:app", **uvicorn_kwargs)


if __name__ == "__main__":
    main()
