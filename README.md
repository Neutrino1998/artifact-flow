# 🤖 ArtifactFlow

> Multi-Agent Research System based on LangGraph and Artifacts

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Latest-green.svg)](https://github.com/langchain-ai/langgraph)

ArtifactFlow 是一个智能多智能体研究系统，通过协调专门的AI智能体来执行综合性研究任务。基于 LangGraph 构建，采用独特的双工件架构，实现 AI 协作研究和人工监督的迭代优化。

## ✨ 核心特性

- **🗂️ 双工件架构**: 分离任务计划和结果工件，实现清晰的工作流管理
- **🤝 多智能体协作**: 专门的智能体（主控、搜索、网页抓取）协调工作
- **⚡ 流式响应**: 实时进度更新和结果生成
- **🎯 人机协作**: 在任意阶段暂停、恢复并提供反馈
- **🔧 灵活工具系统**: 可扩展的工具框架，支持权限控制
- **📊 进度跟踪**: 可视化任务进度和完成状态
- **🔄 迭代优化**: 基于用户反馈的持续改进

## 🛠️ 系统架构

```
 ┌────────────────────────────────────────────────────────────┐
 │                       ARTIFACT LAYER                       │
 │                                                            │
 │  ┌───────────────────────────────┐  ┌────────────────────┐ │
 │  │       Task Plan Artifact      │  │    Result Artifact │ │
 │  │  - 任务分解 & 进度跟踪          │  │  - 最终产出文档     │ │
 │  │  - 智能体共享上下文            │  │  - 用户可编辑       │ │
 │  └───────────────────────────────┘  └────────────────────┘ │
 └────────────────────────────────────────────────────────────┘
           ↑                     ↑                    ↑
      主控智能体               子智能体                用户
    (读写权限)                (只读权限)            (读写权限)
```

### 智能体角色

- **🎯 主控智能体**: 任务协调、信息整合、用户交互
- **🔍 搜索智能体**: 信息检索和结构化搜索结果
- **🕷️ 网页抓取智能体**: 深度内容提取和分析

## 🚀 快速开始

### 环境要求

- Python 3.10+
- API Keys（OpenAI、通义千问、DeepSeek 等）

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

4. **配置环境变量**
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

# ------ Anthropic (Claude) ------
# 获取地址: https://console.anthropic.com/settings/keys
ANTHROPIC_API_KEY=sk-ant-xxx
```

## 💡 支持的模型

### OpenAI
- `gpt-4o` - 最新的 GPT-4 模型
- `gpt-4o-mini` - 轻量级版本

### 通义千问 (Qwen)
- `qwen-turbo` - 快速响应版本
- `qwen-plus` - 增强版本
- `qwen3-30b-thinking` - 支持深度推理的思考模型
- `qwen3-30b-instruct` - 快速指令响应模型

### DeepSeek
- `deepseek-chat` - 对话模型
- `deepseek-reasoner` - 推理模型

## 📁 项目结构

```
artifact-flow/
├── src/
│   ├── core/           # 核心工作流和状态管理
│   ├── agents/         # 智能体实现
│   ├── tools/          # 工具系统和实现
│   ├── models/         # LLM 接口封装
│   ├── utils/          # 工具函数和帮助类
│   └── api/            # API 接口层
├── prompts/            # 智能体提示词模板
├── examples/           # 使用示例
├── logs/               # 日志目录
└── docs/               # 文档
```

## 📈 开发路线图

- [x] **基础设施** (v0.1) - 已完成
  - [x] 项目结构和配置
  - [x] 核心工具模块（日志、重试、XML解析）
  - [x] 多模型LLM接口统一封装

- [ ] **核心实现** (v0.2) - 开发中
  - [ ] Lead Agent 实现
  - [ ] 基础工具系统
  - [ ] Artifact 操作

- [ ] **多智能体系统** (v0.3)
  - [ ] Search Agent 实现
  - [ ] Web Crawl Agent 实现
  - [ ] 智能体通信协议
  - [ ] 工作流编排

- [ ] **高级特性** (v0.4)
  - [ ] 流式响应
  - [ ] 人机协作控制
  - [ ] 错误处理和恢复
  - [ ] 监控和指标

- [ ] **生产就绪** (v1.0)
  - [ ] 性能优化
  - [ ] 安全增强
  - [ ] 完整文档
  - [ ] 部署指南

## 📝 使用示例

```python
# 基础 LLM 调用示例
from src.models.llm import create_llm

# 创建思考模型
llm = create_llm("qwen3-30b-thinking", temperature=0.3)
response = llm.invoke("解释量子计算的基本原理")

# 获取思考过程（如果支持）
if hasattr(response, 'reasoning_content'):
    print("思考过程:", response.reasoning_content)
print("最终回答:", response.content)
```

## 📞 支持与反馈

- 🐛 [问题反馈](https://github.com/Neutrino1998/artifact-flow/issues)
- 💬 [讨论交流](https://github.com/Neutrino1998/artifact-flow/discussions)
- 📖 [开发文档](docs/)
- 📫 [联系作者](mailto:1998neutrino@gmail.com)
