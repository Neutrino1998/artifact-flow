from setuptools import setup, find_packages
from pathlib import Path


def parse_requirements(filename: str) -> list[str]:
    """解析 requirements.txt，忽略注释和空行"""
    requirements = []
    for line in Path(filename).read_text().splitlines():
        line = line.split("#")[0].strip()  # 移除行内注释
        if line:
            requirements.append(line)
    return requirements


setup(
    name="artifact-flow",
    version="0.3.0",
    description="Multi-agent system built on LangGraph",
    author="ArtifactFlow Team",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.11",
    install_requires=parse_requirements("requirements.txt"),
    entry_points={
        "console_scripts": [
            "artifact-flow=cli.main:app",
        ],
    },
)
