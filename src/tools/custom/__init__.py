"""
自定义工具系统
支持通过 MD + YAML frontmatter 声明式定义 HTTP API 工具

loader(load_custom_tools/load_custom_tool)刻意不再导出：external 工具唯一
生产来源是 DB(reconciler seed → snapshot),进程级 MD 加载已无调用方;loader 的
http 构造校验也弱于 seeds/manager 两条活路径(缺 validate_response_extract),
待 #7-full 收编时随模块一并处置。
"""

from .http_tool import HttpTool

__all__ = [
    "HttpTool",
]
