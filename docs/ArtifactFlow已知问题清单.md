# Redis迁移清单

## Phase 1 - 核心（多Worker部署前必须完成）
- [ ] ConversationManager._cache → Redis Hash
- [ ] Streaming Events → Redis Pub/Sub

## Phase 2 - 扩展（并发量上升后）
- [ ] AsyncSqliteSaver → langgraph-checkpoint-redis
- [ ] Redis Key TTL 自动清理

## Phase 3 - 可选优化
- [ ] ArtifactManager 缓存加 TTL（或直接移除）  ← 优先级低
- [ ] API Rate Limit / Session / 分布式锁


# 模型模块已知问题清单

## 次要问题
- [ ] 将langchain模型接入改为专门的万能接口去适应不同provider，例如LiteLLM。尤其要注意reasoning和token usage等字段如何获取

# 工具模块已知问题清单

## 次要问题
- [ ] 使用标准xml解析方式（xml.etree.ElementTree），string字段统一用cdata字段，所有参数Tag平铺，如有下级标签则为list


# Core模块已知问题清单

## 主要问题
- [x] `ExtendableGraph`和`ExecutionController`流式输出支持
- [x] 确认user_confirmation_node在`ExecutionController`层面正确工作，现有代码处理很奇怪：
一个是`handle_permission_confirmation`没有实现对实际工具的调用，同时没有地方调用了这个`handle_permission_confirmation`。另外观察`ExtendableGraph`的路由也不对，`_add_routing_rules`怎么把`user_confirmation`路由到END去了。
- [x] `ContextManager`重复设计了`prepare_context_for_agent`方法。应该与`BaseAgent`中就有`_prepare_context_with_task_plan`做出区分。
- [x] sub agent的response好像不会被lead agent解析。
- [x] `BaseAgent`的messages的拼接方式修改为：
system prompt + instruction + history(如果有) + tool result
- [x] 由于消息拼接被移动到了baseagent外，导致外部调用的工具结果没有被graph记录下来
- [x] conversation history只能format两条？已定位问题：_execute_new_message没有输入parent_message_id应该自动设置parent_message_id关联
- [x] `interrupt()` 函数需要在一个正确的 runnable context 中调用，但现在看起来上下文不正确。已定位问题：异步 interrupt 功能需要 Python 3.11+ 才能正常工作,因为 Python 3.11 之前的版本中异步任务无法正确传播上下文 [Asynchronous Graph with interrupts in Python 3.10 seems to be broken · langchain-ai/langgraph · Discussion #3200](https://github.com/langchain-ai/langgraph/discussions/3200)
- [ ] `langgraph`使用的`AsyncSqliteSaver`需要一个定时清理脚本避免缓存线程无限堆积/或者切换为拥有ttl管理的redis管理
- [x] Agent基类去掉自己的工具调用处理，将工具调用处理全权交给graph的工具节点`user_confirmation_node`(这个node可以改个名字)。例如tool permission为NOTIFY/PUBLIC的时候也交给confirmation node执行，但是自动允许。
- [x] 可观测性设计：仔细设计graph state包含可观测性字段，记录token使用以及工具调用等信息

## 次要问题
- [x] 各类id加上类型名
- [x] controller层的历史记录（User ↔ Graph）和graph层的历史记录是不是也应该分开管理，因为如果都塞到NodeMemory里面的话感觉不太合理，应该是一个context manager管理controller层一个管理graph层，这个context manager可以和前面说的是一个，然后controller层的历史记录以context的形式给graph？
- [x] 引入python-diff-match-patch升级一下现有的artifact_ops.py，并增加版本管理功能与controller中的conversation管理对应起来（算球了，太耦合了），提供更完善的功能给未来开发的前端，例如能生成例如git diff的内容展示模型对文档的修改。
- [x] lead agent提示词提示result artifact 渐进式完成
- [ ] graph level轮次太多提示lead agent
- [x] agent开启关闭debug模式很费劲：删除agentconfig中debug参数
- [x] Typer + Rich实现简易terminal前端
- [ ] agent提示词精简一下
- [x] ToolPromptGenerator中generate_tool_instruction最后return的instruction加上<tool_call_instructions>的tag
- [x] 博查搜索语法确认
- [x] _execute_new_message在调用graph之前，获取了session_id之后清除已有的task_plan
- [x] fetch tool 支持pdf
- [ ] agent logging 加上thread id
- [ ] 区分conversation history和tool interactions的compression level


# Agent模块已知问题清单

## 重要问题
- [x] revisit xml工具解析系统：参数类型转换（str → int/List[str]）应由工具自行处理
- [x] artifact rewrite准确度：update_artifact的old_str匹配失败，需检查read后的格式或正则解析：artifact提供模糊匹配功能
- [x] 优化agent提示词格式：用xml标签规整（如`<task_plan>`），各agent统一
- [x] crawl agent无法正确调用web_fetch：list格式解析问题（JSON数组 vs 多行格式）

## 次要问题
- [x] agent提示词增加系统时间感知
- [x] artifact通用化描述：result artifact的id应根据用户需求动态确定，task_plan支持笔记功能
- [x] agent node对话历史处理：工具循环历史记录需保留并可选择性传递
- [x] task_plan artifact没有及时加载：工具循环中的操作未及时更新到系统提示词
- [x] search agent结果返回方式：考虑直接返回工具结果而非复述
- [x] 精简提示词：简化合并提示词内容
- [x] Agent文件，例如lead_agent.py是不是应该以@property或者别的形式定义工具列表，`create_multi_agent_graph`在`registry.create_agent_toolkit`的时候可以直接传入这个`tool_names`。类似的感觉lead.register_subagent使用的信息应该也通过search和crawl的property获取，而不是编码在`create_multi_agent_graph`中