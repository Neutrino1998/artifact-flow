"""
实例身份 — 进程启动时铸造一次的 instance_id

多副本/多机部署下「哪个实例」的唯一事实源:
- 容器内 hostname 即容器短 id;docker restart 保留容器身份 → 同一实例
  重启前后 id 不变(管理面板同一行连续,靠 started_at 变新看出重启);
- 裸进程(dev / 脚本)退化为机器 hostname;
- `ARTIFACTFLOW_INSTANCE_ID` 环境变量可显式覆盖(编排层注入语义名时用)。

刻意独立小模块、零项目内依赖:utils.logger / middleware / engine /
runtime store 都要它,挂在 config 上会让 utils.logger 反向依赖 config。
"""

import os
import re
import socket


def _mint() -> str:
    raw = os.environ.get("ARTIFACTFLOW_INSTANCE_ID") or socket.gethostname() or "unknown"
    # 会用作日志子目录名与 HTTP 响应头值:收窄字符集(hostname 本就近似此集,
    # 防的是 env 覆盖时手滑塞进路径分隔符/非 ASCII)
    return re.sub(r"[^A-Za-z0-9._-]", "-", raw)[:64] or "unknown"


INSTANCE_ID: str = _mint()
