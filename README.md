# 🤖 ArtifactFlow

> Multi-Agent Research System based on LangGraph and Artifacts

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Latest-green.svg)](https://github.com/langchain-ai/langgraph)
[![Development Status](https://img.shields.io/badge/Status-Alpha%20Development-orange.svg)]()

ArtifactFlow 是一个智能多智能体研究系统，通过协调专门的AI智能体来执行综合性研究任务。基于 LangGraph 构建，采用独特的双 Artifact 架构，实现 AI 协作研究和人工监督的迭代优化。

## ✨ 核心特性

- **🗂️ 双Artifact架构**: 分离任务计划和结果工件，实现清晰的工作流管理
- **🤝 多智能体协作**: 专门的智能体（主控、搜索、网页抓取）协调工作
- **⚡ 流式响应**: 实时进度更新和结果生成
- **🎯 人机协作**: 在任意阶段暂停、恢复并提供反馈
- **🔧 灵活工具系统**: 可扩展的工具框架，支持权限控制
- **🕷️ 智能网页抓取**: 基于crawl4ai的深度内容提取和分析
- **📊 进度跟踪**: 可视化任务进度和完成状态
- **🔄 迭代优化**: 基于用户反馈的持续改进

## 🛠️ 系统架构

```
┌────────────────────────────────────────────────────────────┐
│                       ARTIFACT LAYER                       │
│                                                            │
│  ┌───────────────────────────────┐  ┌────────────────────┐ │
│  │       Task Plan Artifact      │  │    Result Artifact │ │
│  │  - Task breakdown & tracking  │  │  - Final outputs   │ │
│  │  - Shared context for agents  │  │  - User editable   │ │
│  └───────────────────────────────┘  └────────────────────┘ │
└────────────────────────────────────────────────────────────┘
           ↑                     ↑                    ↑
    Lead Agent              Subagents                User
  (Read/Write)             (Read Only)           (Read/Edit)
```

### 智能体角色

- **🎯 主控智能体 (Lead Agent)**: 任务协调、信息整合、用户交互
- **🔍 搜索智能体 (Search Agent)**: 信息检索和结构化搜索结果
- **🕷️ 网页抓取智能体 (Crawl Agent)**: 深度内容提取和分析

### 🎉 已完成模块

- ✅ **工具系统** (v0.1.5) - **已完成**
  - [x] 基础工具框架和权限控制
  - [x] Artifact操作工具 (create/update/rewrite/read)
  - [x] Web搜索工具 (基于博查AI)
  - [x] 智能网页抓取工具 (基于crawl4ai)
  - [x] 工具注册和管理系统

- ✅ **基础设施** (v0.1.0) - **已完成**
  - [x] 项目结构和配置
  - [x] 核心工具模块（日志、重试、XML解析）
  - [x] 多模型LLM接口统一封装

## 🚀 快速开始

### 环境要求

- Python 3.10+
- API Keys（OpenAI、通义千问、DeepSeek、博查AI 等）
- 系统内存 ≥ 8GB（推荐16GB，网页抓取需要启动浏览器）

### 安装步骤

1. **克隆项目**
   ```bash
   git clone https://github.com/yourusername/artifact-flow.git
   cd artifact-flow
   ```

2. **创建虚拟环境**
   ```bash
   # 使用 conda（推荐）
   conda create -n artifact-flow python=3.10
   conda activate artifact-flow
   
   # 或使用 venv
   python -m venv artifact-flow
   # Windows: artifact-flow\Scripts\activate
   # macOS/Linux: source artifact-flow/bin/activate
   ```

3. **安装依赖**
   ```bash
   pip install -r requirements.txt
   pip install -e .
   ```

4. **⚠️ 重要：初始化crawl4ai**
   ```bash
   # crawl4ai 需要额外的初始化步骤
   crawl4ai-setup
   ```
   
   这个命令会：
   - 下载必要的浏览器驱动程序
   - 配置Playwright环境
   
5. **配置环境变量**
   ```bash
   cp .env.example .env
   # 编辑 .env 文件，添加你的 API Keys
   ```

## 🔑 配置指南

创建 `.env` 文件并配置以下 API Keys：

```env
# ========================================
# 模型 API 配置
# ========================================

# ------ OpenAI (GPT系列) ------
# 获取地址: https://platform.openai.com/api-keys
OPENAI_API_KEY=sk-xxx

# ------ 通义千问 (Qwen) ------
# 获取地址: https://dashscope.console.aliyun.com/apiKey
DASHSCOPE_API_KEY=sk-xxx

# ------ DeepSeek ------
# 获取地址: https://platform.deepseek.com/api_keys
DEEPSEEK_API_KEY=sk-xxx

# ========================================
# 工具 API 配置
# ========================================

# ------ 博查AI (Web搜索) ------
# 获取地址: https://open.bochaai.com
BOCHA_API_KEY=sk-xxx
```

## 💡 支持的模型

### OpenAI
- `gpt-4o` - 最新的 GPT-4 模型
- `gpt-4o-mini` - 轻量级版本

### 通义千问 (Qwen)
- `qwen-turbo` - 快速响应版本
- `qwen-plus` - 增强版本
- `qwen3-30b-thinking` - 支持深度推理的思考模型 ⭐
- `qwen3-30b-instruct` - 快速指令响应模型

### DeepSeek
- `deepseek-chat` - 对话模型
- `deepseek-reasoner` - 推理模型 ⭐

## 📁 项目结构

```
artifact-flow/
├── src/
│   ├── core/           # 🚧 核心工作流和状态管理 (开发中)
│   ├── agents/         # 🚧 智能体实现 (开发中)
│   ├── tools/ ✅       # 工具系统和实现 (已完成)
│   │   ├── base.py               # 工具基类和权限定义
│   │   ├── registry.py           # 工具注册和管理
│   │   ├── permissions.py        # 权限控制系统
│   │   ├── prompt_generator.py   # XML提示词生成
│   │   └── implementations/      # 具体工具实现
│   │       ├── artifact_ops.py   # Artifact操作工具
│   │       ├── web_search.py     # 博查AI搜索
│   │       └── web_fetch.py      # crawl4ai网页抓取
│   ├── models/ ✅      # LLM 接口封装 (已完成)
│   │   └── llm.py                # 统一的多模型接口
│   ├── utils/ ✅       # 工具函数和帮助类 (已完成)
│   │   ├── logger.py             # 分级日志系统
│   │   ├── retry.py              # 指数退避重试
│   │   └── xml_parser.py         # 鲁棒XML解析
│   └── api/            # 🚧 API 接口层 (计划中)
├── prompts/            # 智能体提示词模板
├── examples/           # 使用示例
├── logs/               # 日志目录
└── docs/               # 文档
```

## 🧪 使用示例

### 1. 基础LLM调用

```python
from src.models.llm import create_llm

# 创建思考模型
llm = create_llm("qwen3-30b-thinking", temperature=0.3)
response = llm.invoke("解释量子计算的基本原理")

# 获取思考过程
if 'reasoning_content' in response.additional_kwargs:
    print("💭 思考过程:", response.additional_kwargs['reasoning_content'])
print("💬 最终回答:", response.content)
```

### 2. 工具系统使用

```python
import asyncio
from src.tools.implementations.web_search import WebSearchTool
from src.tools.implementations.web_fetch import WebFetchTool
from src.tools.implementations.artifact_ops import CreateArtifactTool

async def demo_tools():
    # 1. 网页搜索
    search_tool = WebSearchTool()
    search_result = await search_tool(
        query="AI多智能体系统最新研究",
        count=5,
        freshness="oneMonth"
    )
    
    if search_result.success:
        print("🔍 搜索完成:", search_result.metadata['results_count'], "条结果")
    
    # 2. 深度网页抓取
    fetch_tool = WebFetchTool()
    urls = ["https://github.com/langchain-ai/langgraph"]
    fetch_result = await fetch_tool(
        urls=urls,
        max_content_length=3000,
        max_concurrent=2
    )
    
    if fetch_result.success:
        print("🕷️ 抓取完成:", fetch_result.metadata['success_count'], "个页面")
    
    # 3. 创建研究工件
    artifact_tool = CreateArtifactTool()
    create_result = await artifact_tool(
        id="research_plan",
        type="task_plan",
        title="Multi-Agent系统研究计划",
        content="# 研究目标\n\n1. 分析当前技术现状\n2. 设计系统架构"
    )
    
    if create_result.success:
        print("📄 工件创建成功")

# 运行演示
asyncio.run(demo_tools())
```

### 3. 测试已完成模块

```bash
# 测试LLM接口
python -m src.models.llm

# 测试工具系统
python -m src.tools.implementations.web_search
python -m src.tools.implementations.web_fetch
python -m src.tools.implementations.artifact_ops

# 测试工具注册系统
python -m src.tools.registry

# 测试权限系统
python -m src.tools.permissions
```

## 📈 开发路线图

- ✅ **基础设施** (v0.1) - **已完成**
  - [x] 项目结构和配置
  - [x] 核心工具模块（日志、重试、XML解析）
  - [x] 多模型LLM接口统一封装

- ✅ **工具系统** (v0.1.5) - **已完成**
  - [x] 工具框架和权限控制
  - [x] Artifact操作工具
  - [x] Web搜索和抓取工具
  - [x] XML提示词生成系统

- 🚧 **核心实现** (v0.2) - **开发中**
  - [ ] Agent状态管理 (core/state.py)
  - [ ] LangGraph工作流 (core/graph.py)
  - [ ] 执行控制器 (core/controller.py)
  - [ ] Lead Agent 实现

- 🎯 **多智能体系统** (v0.3) - **计划中**
  - [ ] Search Agent 实现
  - [ ] Web Crawl Agent 实现
  - [ ] 智能体通信协议
  - [ ] 工作流编排

- 🚀 **高级特性** (v0.4) - **计划中**
  - [ ] 流式响应
  - [ ] 人机协作控制
  - [ ] 错误处理和恢复
  - [ ] 监控和指标

- 🎉 **生产就绪** (v1.0) - **目标**
  - [ ] 性能优化
  - [ ] 安全增强
  - [ ] 完整文档
  - [ ] 部署指南


## 📞 支持与反馈

- 🐛 [问题反馈](https://github.com/Neutrino1998/artifact-flow/issues)
- 💬 [讨论交流](https://github.com/Neutrino1998/artifact-flow/discussions)
- 📖 [开发文档](docs/)
- 📫 [联系作者](mailto:1998neutrino@gmail.com)

---

⭐ **如果这个项目对你有帮助，请给我们一个Star！**