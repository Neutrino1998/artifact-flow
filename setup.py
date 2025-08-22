from setuptools import setup, find_packages

setup(
    name="artifact-flow",
    version="0.1.0",
    packages=find_packages(where="src"),  # 指定在 src 目录下查找包
    package_dir={"": "src"},              # 告诉 setuptools 包的根目录是 src
    python_requires=">=3.10",
)