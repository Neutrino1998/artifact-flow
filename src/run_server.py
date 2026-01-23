#!/usr/bin/env python3
"""
ArtifactFlow API 服务器启动脚本

使用方式:
    python run_server.py
    python run_server.py --host 0.0.0.0 --port 8000
    python run_server.py --reload  # 开发模式
"""

import argparse
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

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                    ArtifactFlow API Server                   ║
╠══════════════════════════════════════════════════════════════╣
║  Host: {args.host:<53} ║
║  Port: {args.port:<53} ║
║  Workers: {args.workers:<50} ║
║  Reload: {str(args.reload):<51} ║
║  Log Level: {args.log_level:<48} ║
╚══════════════════════════════════════════════════════════════╝
""")

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,  # reload 模式只能用单 worker
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
