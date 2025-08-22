# 🤖 ArtifactFlow

> Multi-Agent Research System based on LangGraph and Artifacts

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Latest-green.svg)](https://github.com/langchain-ai/langgraph)

ArtifactFlow is an intelligent multi-agent research system that orchestrates specialized AI agents to conduct comprehensive research tasks. Built on LangGraph with a unique dual-artifact architecture, it enables collaborative AI research with human oversight and iterative refinement.

## ✨ Key Features

- **🏗️ Dual-Artifact Architecture**: Separate task planning and result artifacts for clear workflow management
- **🤝 Multi-Agent Collaboration**: Specialized agents (Lead, Search, Web Crawl) working in coordination
- **⚡ Streaming Responses**: Real-time progress updates and result generation
- **🎯 Human-in-the-Loop**: Pause, resume, and provide feedback at any stage
- **🔧 Flexible Tool System**: Extensible tool framework with permission controls
- **📊 Progress Tracking**: Visual task progress and completion status
- **🔄 Iterative Refinement**: Continuous improvement based on user feedback

## 🏛️ System Architecture

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

### Agent Roles

- **🎯 Lead Agent**: Task coordination, information integration, user interaction
- **🔍 Search Agent**: Information retrieval and structured search results
- **🕷️ Web Crawl Agent**: Deep content extraction and analysis
- **➕ Extensible**: Easy to add specialized agents for specific domains

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- Conda (recommended) or pip
- API keys for supported LLM providers (OpenAI, Anthropic, etc.)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/artifact-flow.git
   cd artifact-flow
   ```

2. **Create and activate environment**
   ```bash
   # Using conda (recommended)
   conda create -n artifact-flow python=3.10
   conda activate artifact-flow
   
   # Or using venv
   python -m venv artifact-flow
   # Windows: artifact-flow\Scripts\activate
   # macOS/Linux: source artifact-flow/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install -e .
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys and configuration
   ```

5. **Run the system**
   ```bash
   python -m src.api.server
   ```

## 📋 Configuration

### Environment Variables (.env)

```env
# API Keys
OPENAI_API_KEY=your_openai_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here

# Model Configuration
DEFAULT_MODEL_PROVIDER=openai
DEFAULT_MODEL_NAME=gpt-4-turbo

# Server Configuration
API_HOST=localhost
API_PORT=8000
```

### Model Configuration (config.yaml)

```yaml
models:
  lead_agent:
    provider: "anthropic"
    model: "claude-3-opus"
    supports_thinking: true
  search_agent:
    provider: "openai"
    model: "gpt-4-turbo"
```

## 💡 Usage Examples

### Basic Research Task

```python
from src.core.controller import ExecutionController

# Initialize the system
controller = ExecutionController()

# Start a research task
result = await controller.execute_task(
    "Research the latest developments in AI safety regulations"
)

# Monitor progress through artifacts
task_plan = result.get_artifact("task_plan")
research_results = result.get_artifact("results")
```

### Human-in-the-Loop Workflow

```python
# Pause execution for review
await controller.pause(thread_id)

# Add additional context
controller.add_context(
    "Please focus more on European regulations"
)

# Resume with new context
await controller.resume(thread_id)
```

## 🔧 Development

### Project Structure

```
artifact-flow/
├── src/
│   ├── core/           # Core workflow and state management
│   ├── agents/         # Agent implementations
│   ├── tools/          # Tool system and implementations
│   ├── models/         # LLM interfaces
│   ├── utils/          # Utilities and helpers
│   └── api/            # API and server components
├── prompts/            # Agent prompt templates
├── tests/              # Test suite
├── examples/           # Usage examples
└── docs/               # Documentation
```

### Adding New Agents

1. Inherit from `BaseAgent` in `src/agents/base.py`
2. Implement required methods: `execute()`, `get_tools()`
3. Register in the workflow graph
4. Add configuration to `config.yaml`

### Adding New Tools

1. Inherit from `BaseTool` in `src/tools/base.py`
2. Define tool schema and permissions
3. Register in `src/tools/registry.py`
4. Add to agent tool lists

## 🧪 Testing

```bash
# Run all tests
python -m pytest tests/

# Run specific test category
python -m pytest tests/agents/
python -m pytest tests/tools/

# Run with coverage
python -m pytest --cov=src tests/
```

## 📈 Roadmap

- [ ] **Core Implementation** (v0.1)
  - [x] Project structure and configuration
  - [ ] Lead Agent implementation
  - [ ] Basic tool system
  - [ ] Artifact operations

- [ ] **Multi-Agent System** (v0.2)
  - [ ] Search Agent implementation
  - [ ] Web Crawl Agent implementation
  - [ ] Agent communication protocol
  - [ ] Workflow orchestration

- [ ] **Advanced Features** (v0.3)
  - [ ] Streaming responses
  - [ ] Human-in-the-loop controls
  - [ ] Error handling and recovery
  - [ ] Monitoring and metrics

- [ ] **Production Ready** (v1.0)
  - [ ] Performance optimization
  - [ ] Security enhancements
  - [ ] Documentation completion
  - [ ] Deployment guides

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'Add amazing feature'`
4. Push to the branch: `git push origin feature/amazing-feature`
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Built on [LangGraph](https://github.com/langchain-ai/langgraph) for workflow orchestration
- Powered by [LangChain](https://github.com/langchain-ai/langchain) for LLM interactions
- Inspired by multi-agent research methodologies

## 📞 Support

- 📖 [Documentation](docs/)
- 🐛 [Issue Tracker](https://github.com/yourusername/artifact-flow/issues)
- 💬 [Discussions](https://github.com/yourusername/artifact-flow/discussions)

---

**Built with ❤️ for the AI research community**