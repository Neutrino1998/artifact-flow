"""
执行控制器
管理LangGraph工作流的执行、暂停、恢复等操作
"""

from typing import Dict, Any, Optional, List, AsyncGenerator
from uuid import uuid4
from datetime import datetime
import asyncio

from core.state import create_initial_state, AgentState
from core.graph import create_default_graph
from utils.logger import get_logger
from tools.implementations.artifact_ops import _artifact_store

logger = get_logger("Controller")


class ExecutionController:
    """
    执行控制器
    
    负责管理Multi-Agent系统的执行生命周期
    """
    
    def __init__(self, graph=None):
        """
        初始化控制器
        
        Args:
            graph: LangGraph编译后的工作流（可选）
        """
        self.graph = graph or create_default_graph()
        self.active_threads = {}  # 活跃的执行线程
        self.execution_history = []  # 执行历史
        
        logger.info("ExecutionController initialized")
    
    async def start_task(
        self,
        task: str,
        session_id: Optional[str] = None,
        context_level: str = "normal"
    ) -> Dict[str, Any]:
        """
        启动新任务
        
        Args:
            task: 任务描述
            session_id: 会话ID（可选）
            context_level: 上下文级别
            
        Returns:
            包含thread_id和初始状态的字典
        """
        # 生成IDs
        thread_id = str(uuid4())
        if not session_id:
            session_id = str(uuid4())
        
        # 设置artifact store的session
        _artifact_store.set_session(session_id)
        
        # 创建初始状态
        initial_state = create_initial_state(
            task=task,
            session_id=session_id,
            thread_id=thread_id,
            context_level=context_level
        )
        
        # 记录线程信息
        self.active_threads[thread_id] = {
            "status": "running",
            "task": task,
            "session_id": session_id,
            "started_at": datetime.now().isoformat(),
            "checkpoints": []
        }
        
        # 配置
        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": f"session_{session_id}"
            }
        }
        
        logger.info(f"Starting task: {task[:100]}... (thread: {thread_id})")
        
        # 启动执行
        try:
            # 异步执行graph
            result = await self.graph.ainvoke(initial_state, config)
            
            # 更新线程状态
            self.active_threads[thread_id]["status"] = "completed"
            self.active_threads[thread_id]["completed_at"] = datetime.now().isoformat()
            
            # 记录历史
            self.execution_history.append({
                "thread_id": thread_id,
                "task": task,
                "status": "completed",
                "timestamp": datetime.now().isoformat()
            })
            
            logger.info(f"Task completed: {thread_id}")
            
            return {
                "thread_id": thread_id,
                "session_id": session_id,
                "status": "completed",
                "final_state": result,
                "artifacts": _artifact_store.list_artifacts()
            }
            
        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            
            # 更新线程状态
            self.active_threads[thread_id]["status"] = "failed"
            self.active_threads[thread_id]["error"] = str(e)
            
            return {
                "thread_id": thread_id,
                "session_id": session_id,
                "status": "failed",
                "error": str(e)
            }
    
    async def stream_task(
        self,
        task: str,
        session_id: Optional[str] = None,
        context_level: str = "normal"
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式执行任务
        
        Args:
            task: 任务描述
            session_id: 会话ID
            context_level: 上下文级别
            
        Yields:
            执行事件
        """
        # 生成IDs
        thread_id = str(uuid4())
        if not session_id:
            session_id = str(uuid4())
        
        # 设置artifact store的session
        _artifact_store.set_session(session_id)
        
        # 创建初始状态
        initial_state = create_initial_state(
            task=task,
            session_id=session_id,
            thread_id=thread_id,
            context_level=context_level
        )
        
        # 记录线程信息
        self.active_threads[thread_id] = {
            "status": "running",
            "task": task,
            "session_id": session_id,
            "started_at": datetime.now().isoformat()
        }
        
        # 配置
        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": f"session_{session_id}"
            },
            "stream_mode": "values"  # 流式输出模式
        }
        
        logger.info(f"Starting streaming task: {task[:100]}... (thread: {thread_id})")
        
        # Yield开始事件
        yield {
            "type": "start",
            "thread_id": thread_id,
            "session_id": session_id,
            "task": task,
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            # 流式执行
            async for chunk in self.graph.astream(initial_state, config):
                # Yield中间状态
                yield {
                    "type": "state_update",
                    "thread_id": thread_id,
                    "state": chunk,
                    "timestamp": datetime.now().isoformat()
                }
                
                # 检查是否需要中断
                if chunk.get("interrupt_before"):
                    yield {
                        "type": "interrupt",
                        "thread_id": thread_id,
                        "reason": "confirmation_required",
                        "pending": chunk.get("pending_confirmation"),
                        "timestamp": datetime.now().isoformat()
                    }
                    
                    # 等待确认
                    self.active_threads[thread_id]["status"] = "paused"
                    break
            
            # 检查最终状态
            if self.active_threads[thread_id]["status"] != "paused":
                self.active_threads[thread_id]["status"] = "completed"
                
                # Yield完成事件
                yield {
                    "type": "complete",
                    "thread_id": thread_id,
                    "session_id": session_id,
                    "artifacts": _artifact_store.list_artifacts(),
                    "timestamp": datetime.now().isoformat()
                }
            
        except Exception as e:
            logger.error(f"Streaming task failed: {e}")
            
            # 更新状态
            self.active_threads[thread_id]["status"] = "failed"
            self.active_threads[thread_id]["error"] = str(e)
            
            # Yield错误事件
            yield {
                "type": "error",
                "thread_id": thread_id,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    async def confirm_tool(
        self,
        thread_id: str,
        approved: bool,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        处理工具确认
        
        Args:
            thread_id: 线程ID
            approved: 是否批准
            reason: 批准/拒绝原因
            
        Returns:
            确认结果
        """
        if thread_id not in self.active_threads:
            return {
                "success": False,
                "error": f"Thread {thread_id} not found"
            }
        
        thread_info = self.active_threads[thread_id]
        if thread_info["status"] != "paused":
            return {
                "success": False,
                "error": f"Thread {thread_id} is not paused"
            }
        
        logger.info(f"Tool confirmation for thread {thread_id}: approved={approved}")
        
        # 更新状态
        update_data = {
            "tool_confirmation": {
                "approved": approved,
                "reason": reason,
                "timestamp": datetime.now().isoformat()
            },
            "pending_confirmation": None,  # 清除待确认
            "interrupt_before": None  # 清除中断标记
        }
        
        # 配置
        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": f"session_{thread_info['session_id']}"
            }
        }
        
        # 恢复执行
        try:
            # 更新graph状态并继续执行
            await self.graph.aupdate(update_data, config)
            
            # 更新线程状态
            thread_info["status"] = "running"
            
            return {
                "success": True,
                "thread_id": thread_id,
                "action": "approved" if approved else "rejected"
            }
            
        except Exception as e:
            logger.error(f"Failed to confirm tool: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def pause_task(self, thread_id: str) -> Dict[str, Any]:
        """
        暂停任务执行
        
        Args:
            thread_id: 线程ID
            
        Returns:
            暂停结果
        """
        if thread_id not in self.active_threads:
            return {
                "success": False,
                "error": f"Thread {thread_id} not found"
            }
        
        thread_info = self.active_threads[thread_id]
        if thread_info["status"] != "running":
            return {
                "success": False,
                "error": f"Thread {thread_id} is not running"
            }
        
        # 设置暂停标记
        thread_info["status"] = "paused"
        thread_info["paused_at"] = datetime.now().isoformat()
        
        logger.info(f"Task paused: {thread_id}")
        
        return {
            "success": True,
            "thread_id": thread_id,
            "status": "paused"
        }
    
    async def resume_task(
        self,
        thread_id: str,
        additional_context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        恢复任务执行
        
        Args:
            thread_id: 线程ID
            additional_context: 额外的上下文
            
        Returns:
            恢复结果
        """
        if thread_id not in self.active_threads:
            return {
                "success": False,
                "error": f"Thread {thread_id} not found"
            }
        
        thread_info = self.active_threads[thread_id]
        if thread_info["status"] != "paused":
            return {
                "success": False,
                "error": f"Thread {thread_id} is not paused"
            }
        
        logger.info(f"Resuming task: {thread_id}")
        
        # 配置
        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": f"session_{thread_info['session_id']}"
            }
        }
        
        # 准备更新数据
        update_data = {
            "execution_status": "running"
        }
        
        if additional_context:
            update_data["metadata"] = additional_context
        
        # 恢复执行
        try:
            await self.graph.aupdate(update_data, config)
            
            # 更新线程状态
            thread_info["status"] = "running"
            thread_info["resumed_at"] = datetime.now().isoformat()
            
            return {
                "success": True,
                "thread_id": thread_id,
                "status": "running"
            }
            
        except Exception as e:
            logger.error(f"Failed to resume task: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_thread_status(self, thread_id: str) -> Optional[Dict]:
        """
        获取线程状态
        
        Args:
            thread_id: 线程ID
            
        Returns:
            线程状态信息
        """
        return self.active_threads.get(thread_id)
    
    def list_active_threads(self) -> List[Dict]:
        """
        列出所有活跃线程
        
        Returns:
            活跃线程列表
        """
        active = []
        for thread_id, info in self.active_threads.items():
            if info["status"] in ["running", "paused"]:
                active.append({
                    "thread_id": thread_id,
                    "task": info["task"][:100] + "..." if len(info["task"]) > 100 else info["task"],
                    "status": info["status"],
                    "started_at": info["started_at"]
                })
        return active
    
    def cleanup_completed_threads(self, older_than_hours: int = 24):
        """
        清理已完成的线程
        
        Args:
            older_than_hours: 清理多少小时前的线程
        """
        from datetime import datetime, timedelta
        
        cutoff_time = datetime.now() - timedelta(hours=older_than_hours)
        threads_to_remove = []
        
        for thread_id, info in self.active_threads.items():
            if info["status"] in ["completed", "failed"]:
                # 检查完成时间
                completed_at = info.get("completed_at", info.get("started_at"))
                if completed_at:
                    completed_time = datetime.fromisoformat(completed_at)
                    if completed_time < cutoff_time:
                        threads_to_remove.append(thread_id)
        
        for thread_id in threads_to_remove:
            del self.active_threads[thread_id]
        
        if threads_to_remove:
            logger.info(f"Cleaned up {len(threads_to_remove)} completed threads")
        
        return len(threads_to_remove)