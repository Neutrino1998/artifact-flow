from setuptools import setup, find_packages

setup(
    name="artifact-flow",
    version="0.3.0",
    description="Multi-agent system built on LangGraph",
    author="ArtifactFlow Team",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.11",
    install_requires=[
        # LangChain 核心组件
        "langchain>=0.3.0",
        "langchain-core>=0.3.0",
        "langchain-openai>=0.2.0",
        "langgraph>=0.2.0",
        "langgraph-checkpoint>=2.0.0",
        "langgraph-checkpoint-sqlite>=2.0.0",
        # 模型提供商
        "dashscope>=1.14.0",
        "langchain-community>=0.3.0",
        "langchain-deepseek>=0.1.0",
        # 工具依赖
        "python-dotenv>=1.0.0",
        "crawl4ai>=0.3.0",
        "beautifulsoup4>=4.11.0",
        "pypdf>=4.0.0",
        "aiohttp>=3.9.0",
        "diff-match-patch>=20230430",
        # 数据库
        "sqlalchemy[asyncio]>=2.0.0",
        "aiosqlite==0.21.0",
        # API 依赖
        "fastapi>=0.109.0",
        "uvicorn[standard]>=0.27.0",
        "pydantic-settings>=2.0.0",
        "aiofiles>=23.2.0",
        # CLI 依赖
        "typer>=0.9.0",
        "rich>=13.0.0",
    ],
    extras_require={
        "dev": [
            "httpx>=0.26.0",
            "pytest-asyncio>=0.23.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "artifact-flow=cli.main:app",
        ],
    },
)
