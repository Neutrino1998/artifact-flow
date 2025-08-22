# Reverse engineering Claude Code

**Author:** Reid Barber
**Last updated:** Sunday, March 30, 2025
**Original post:** https://www.reidbarber.com/blog/reverse-engineering-claude-code

Anthropic recently released a research preview of [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview), an agentic coding tool that lets developers use Anthropic's Claude models to read and update code directly from the terminal. While it currently isn't open source, it is deployed to [NPM](https://www.npmjs.com/package/@anthropic-ai/claude-code) and was initially released with source maps. As a sequel to my [Reverse engineering Claude Artifacts](https://www.reidbarber.com/blog/reverse-engineering-claude-artifacts) post, this post will analyze how Claude Code works behind the scenes.

## Overview

Claude Code uses a [REPL (Read-Eval-Print Loop)](https://en.wikipedia.org/wiki/Read–eval–print_loop), allowing users to enter prompts in natural language or use specific slash commands for predefined actions. The application processes this input and either handles it directly for local operations, or constructs and sends queries to the backend language model.

A key feature is its suite of tools that the model can request to use. These tools extend its capabilities to include interacting with the local filesystem (reading, writing, searching files), executing shell commands, managing Jupyter notebooks, and even delegating complex tasks to sub-agents. A permissions system ensures users maintain control over actions that affect their system or files. The architecture separates the text-based user interface, core logic, external service interactions, tool implementations, and state management. It also supports extending functionality through external tools via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/).

## Main Components

- **UI Layer**: Handles the terminal interface presentation using [React](https://react.dev/) and the [Ink](https://github.com/vadimdemedes/ink) library. This includes the main REPL screen, rendering conversation messages, displaying prompts for user input, managing permission requests, and presenting onboarding or configuration dialogs.
- **Core Logic**: Orchestrates the application flow. It parses user input, determines whether it's a prompt for the model or a command, manages the conversation state, interacts with the model model via the Claude API Service, and coordinates the use of tools when requested by the model.
- **Services**: Modules responsible for external communication and core functionalities. Key services include the Claude API Service (for model interaction), Statsig Service (for feature flags and analytics), Sentry Service (for error reporting), MCP Client Service (for connecting to external tools), OAuth Service (for user authentication), and the Notifier Service (for desktop notifications). These are all described in more detail in the Services section below.
- **Tools**: Functions that the model can invoke. Examples include the BashTool for command execution, FileReadTool/FileWriteTool/FileEditTool for filesystem operations, GlobTool/GrepTool for searching, AgentTool for launching sub-tasks, and the MCPTool wrapper for external protocol tools. These are all described in more detail in the Tools section below.
- **Data and State Management**: Components responsible for managing persistent and session data. This includes configuration management (global and project-specific settings, API keys, tool permissions), command history, conversation context gathering, session state, and API cost tracking.
- **Utilities**: Shared helper functions for things like filesystem interactions, git operations, message processing, and command parsing.

## Data Flow

1. **User Input**: The user provides input via the terminal UI.
2. **Input Processing**: The Core Logic layer receives the input and determines its type:
   - **Bash Command**: If identified as a bash command, it's routed to the BashTool for execution. Output is returned to the UI.
   - **Slash Command**: If identified as a slash command, the Command Executor handles it. This might involve executing local logic, rendering a specific UI component, or formatting a prompt for the model.
   - **Prompt**: If it's a natural language prompt, it's packaged as a user message for the model.
3. **LLM Query Construction**: The Core Logic prepares a request for the Claude API Service. It gathers the conversation history, relevant context (like project information or code style guidelines), and the latest user message or command-generated prompt.
4. **API Request**: The request is sent to the backend model via the Claude API Service.
5. **LLM Response Processing**: The Claude API Service receives the response.
   - **Text Response**: The model's textual answer is passed back to the UI Layer for display.
   - **Tool Use Request**: If the model requests to use one or more tools, the Core Logic identifies the requested tool(s).
6. **Tool Execution Cycle**:
   - **Permission Check**: For each requested tool use, the Permissions component verifies if the action is permitted based on configuration or prior user approval.
   - **User Prompt (if needed)**: If permission is required, the UI Layer displays a permission request dialog. User approval (temporary or permanent) allows execution; denial sends a rejection message back to the model.
   - **Tool Invocation**: If permitted, the Core Logic invokes the specific tool's execution logic (e.g., FileEditTool attempts to modify a file, GrepTool searches file contents).
   - **Tool Result**: The tool returns its result (data and/or a summary message for the model).
7. **Follow-up Query (if tools were used)**: The Core Logic sends the original messages, the model's tool request, and the collected tool results back to the Claude API Service to get a final response based on the tool outcomes.
8. **Final Display**: The model's final text response is rendered in the UI.
9. **Background Updates**: Throughout this flow, components like the Cost Tracker, History manager, and Configuration manager update their respective states. Event logging occurs via the Statsig Service and error reporting via the Sentry Service.

An example of this flow is shown in the sequence diagram below:

![Sequence diagram showing application flow for user input (Bash command, Slash command, or Prompt). Details interactions between UI, Core Logic, Permissions, Tool Execution, and Claude API. Highlights permission checks for executing commands/tools and the different processing paths, including AI interaction with Claude and potential tool use requests.](D:\workspace\code\my-artifact\docs\claude-code-flow-dark.svg)

## Permission System

A permission system is used to control model-initiated actions. This applies to tools that modify files (FileWriteTool, FileEditTool, NotebookEditTool), execute commands (BashTool), or interact with external systems (MCPTool). Read-only tools need permission only when accessing files outside the project directory.

**Permission Flow:**

- Initial trust dialog grants baseline read access
- System checks configuration for pre-approved permissions
- If not pre-approved, user is presented a permission request dialog
- User can grant temporarily or permanently
- Rejection prevents tool execution and notifies the model

**Special Cases:**

- File modification permission also grants session-only write access to project directory
- MCP server connections require explicit approval
- A command-line override exists for isolated environments

## Services

### Claude API Service

Handles all interactions with the [Anthropic API](https://www.anthropic.com/api) (supporting direct, [Bedrock](https://aws.amazon.com/bedrock/claude/), and [Vertex](https://cloud.google.com/vertex-ai/generative-ai/docs/partner-models/use-claude) endpoints). Manages API key authentication, request retries with exponential backoff, cost calculation based on token usage, prompt caching logic, and response streaming/parsing. It's the core interface to the model.

### Statsig Service

Integrates with the [Statsig](https://www.statsig.com/) feature flagging and experimentation platform. Initializes the Statsig client, logs events for analytics and monitoring, checks feature gates, and retrieves dynamic configurations and experiment values.

### Sentry Service

Integrates with the [Sentry](https://sentry.io/) error reporting platform. Initializes the Sentry client and provides a function to send runtime errors and relevant context (like user ID, session ID, environment details) to Sentry for debugging and monitoring.

### MCP Client Service

Manages connections to external [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) servers defined in configuration. Connects to servers (via Standard Input/Output or Server-Sent Events), lists available tools and commands from them, and proxies requests to the appropriate server. Handles MCP server approval status.

### OAuth Service

Manages the OAuth 2.0 flow for user authentication with the [Anthropic Console](https://console.anthropic.com/). Handles generating authorization URLs, starting a local server to receive the callback, exchanging the authorization code for an access token, and using the token to create and store a primary API key in the global config.

### Notifier Service

Provides desktop notification functionality based on user preference (iTerm2 proprietary escape codes, terminal bell, or disabled). Used to alert the user when the application requires attention after a period of inactivity.

### VCR

A testing utility (Visual Cassette Recorder) used only in test environments (NODE_ENV === 'test'). It records actual Anthropic API request/response pairs to fixture files on the first run and replays them on subsequent runs, allowing tests to run without hitting the live API.

## Tools

### ArchitectTool

Analyzes technical requirements or coding tasks and generates a detailed, step-by-step implementation plan. It leverages other read-only filesystem tools (like FileReadTool, GlobTool, GrepTool, LSTool) to gather context but does not write code or modify files itself. Designed to provide architectural guidance.

### AgentTool

Launches a new, independent agent instance to perform a specific, autonomous task (like complex searches or analysis). The agent has access to a subset of tools (primarily read-only filesystem tools by default) and returns a final report upon completion. Useful for parallelizing tasks or performing multi-step operations without cluttering the main conversation.

### BashTool

Executes arbitrary bash commands within a persistent shell session. It maintains the shell's state (like the current working directory and environment variables) across multiple calls. Includes security checks for banned commands and specific handling for git operations and pull request creation via gh. Requires user permission for execution.

### FileEditTool

Edits files by replacing a unique occurrence of an old_string with a new_string. Requires significant context (surrounding lines) in old_string to ensure uniqueness and prevent unintended modifications. Can also create new files if old_string is empty. Requires user permission.

### FileReadTool

Reads the content of a specified file from the local filesystem. Supports reading text files (with optional line offsets/limits for large files) and image files (returning base64 encoded data). Requires user permission for paths outside the initial project directory.

### FileWriteTool

Writes or overwrites the entire content of a specified file on the local filesystem. Creates parent directories if they don't exist. Used for creating new files or making substantial changes where FileEditTool is unsuitable. Requires user permission.

### GlobTool

Performs fast file searching using glob patterns (e.g., **/*.js). Returns a list of matching file paths, sorted by modification time, within a specified directory (or current working directory by default). Primarily used for finding files based on naming patterns. Requires user permission for paths outside the initial project directory.

### GrepTool

Searches the content of files within a specified directory (or current working directory) using regular expression patterns. Can filter files to search using include patterns (like *.ts). Returns a list of files containing matches, sorted by modification time. Requires user permission for paths outside the initial project directory.

### LSTool

Lists files and directories within a specified path, similar to the ls command but with a structured tree output. Used for basic directory exploration. Requires user permission for paths outside the initial project directory.

### MemoryReadTool

Reads content from the application's persistent memory directory. Can read a specific file or list all files and the content of a root memory file (index.md).

### MemoryWriteTool

Writes content to a specified file within the application's persistent memory directory, creating directories as needed.

### NotebookEditTool

Specifically designed to edit [Jupyter Notebook](https://jupyter.org/) (.ipynb) files. Allows replacing, inserting, or deleting entire cells by index. Handles the JSON structure of notebooks correctly. Requires user permission.

### NotebookReadTool

Reads and extracts content (code and markdown cells) and outputs (text and images) from Jupyter Notebook (.ipynb) files. Parses the notebook structure and presents it to the model. Requires user permission for paths outside the initial project directory.

### StickerRequestTool

An easter egg tool triggered when a user asks for stickers. Renders an interactive form in the terminal to collect shipping information. Submits data via Statsig events.

### ThinkTool

A tool designed for the model to log its thought process or reasoning steps without performing any external action or filesystem modification. Inspired by [tau-bench](https://github.com/sierra-research/tau-bench), it helps in understanding the model's plan or analysis for complex tasks. Output is primarily for logging/debugging.

### MCPTool

A generic wrapper representing tools provided by external Model Context Protocol (MCP) servers. The specific name, description, and functionality are dynamically loaded from connected MCP servers via the MCPClientService. Handles communication and rendering for these external tools. Requires user permission.

## Conclusion

Claude Code's architecture reveals just how effective combining simple tools and concepts can be when a powerful model is doing the hard work. Sometimes text and tools are all you need.
