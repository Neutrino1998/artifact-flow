"""导出 FastAPI OpenAPI schema 到 frontend/src/types/openapi.json

用法：python scripts/export_openapi.py

每次后端 API schema 变更后运行，前端再执行 npm run generate-types 刷新 TS 类型。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api.main import app

output_path = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "src", "types", "openapi.json"
)
os.makedirs(os.path.dirname(output_path), exist_ok=True)

with open(output_path, "w") as f:
    json.dump(app.openapi(), f, indent=2)

print(f"OpenAPI schema exported to {output_path}")
