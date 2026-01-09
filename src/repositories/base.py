"""
Repository 基类

提供所有 Repository 的公共接口和辅助方法。
遵循 ORM Only 规范：所有数据库操作必须通过 SQLAlchemy ORM 进行。
"""

from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Optional, List, Any, Type
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Base


# 泛型类型变量
T = TypeVar("T", bound=Base)


class BaseRepository(ABC, Generic[T]):
    """
    Repository 抽象基类
    
    职责：
    - 封装数据库 CRUD 操作
    - 提供统一的查询接口
    - 遵循 ORM Only 规范
    
    使用方式：
        class ConversationRepository(BaseRepository[Conversation]):
            def __init__(self, session: AsyncSession):
                super().__init__(session, Conversation)
    """
    
    def __init__(self, session: AsyncSession, model_class: Type[T]):
        """
        初始化 Repository
        
        Args:
            session: SQLAlchemy 异步 Session（由依赖注入提供）
            model_class: ORM 模型类
        """
        self._session = session
        self._model_class = model_class
    
    @property
    def session(self) -> AsyncSession:
        """获取当前 Session"""
        return self._session
    
    @property
    def model_class(self) -> Type[T]:
        """获取模型类"""
        return self._model_class
    
    # ========================================
    # 通用 CRUD 操作
    # ========================================
    
    async def get_by_id(self, id: Any) -> Optional[T]:
        """
        根据主键获取实体
        
        Args:
            id: 主键值
            
        Returns:
            实体对象，不存在则返回 None
        """
        return await self._session.get(self._model_class, id)
    
    async def get_all(
        self,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[T]:
        """
        获取所有实体
        
        Args:
            limit: 限制返回数量
            offset: 跳过前 N 条
            
        Returns:
            实体列表
        """
        query = select(self._model_class)
        
        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
        
        result = await self._session.execute(query)
        return list(result.scalars().all())
    
    async def count(self) -> int:
        """
        获取实体总数
        
        Returns:
            实体数量
        """
        query = select(func.count()).select_from(self._model_class)
        result = await self._session.execute(query)
        return result.scalar_one()
    
    async def exists(self, id: Any) -> bool:
        """
        检查实体是否存在
        
        Args:
            id: 主键值
            
        Returns:
            是否存在
        """
        entity = await self.get_by_id(id)
        return entity is not None
    
    async def add(self, entity: T) -> T:
        """
        添加新实体
        
        Args:
            entity: 实体对象
            
        Returns:
            添加后的实体（含自动生成的字段）
        """
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return entity
    
    async def add_all(self, entities: List[T]) -> List[T]:
        """
        批量添加实体
        
        Args:
            entities: 实体列表
            
        Returns:
            添加后的实体列表
        """
        self._session.add_all(entities)
        await self._session.flush()
        for entity in entities:
            await self._session.refresh(entity)
        return entities
    
    async def update(self, entity: T) -> T:
        """
        更新实体
        
        注意：此方法假设实体已在 Session 中。
        如果需要乐观锁，请使用子类的特定方法。
        
        Args:
            entity: 实体对象
            
        Returns:
            更新后的实体
        """
        await self._session.flush()
        await self._session.refresh(entity)
        return entity
    
    async def delete(self, entity: T) -> None:
        """
        删除实体
        
        Args:
            entity: 实体对象
        """
        await self._session.delete(entity)
        await self._session.flush()
    
    async def delete_by_id(self, id: Any) -> bool:
        """
        根据主键删除实体
        
        Args:
            id: 主键值
            
        Returns:
            是否成功删除
        """
        entity = await self.get_by_id(id)
        if entity:
            await self.delete(entity)
            return True
        return False
    
    # ========================================
    # 辅助方法
    # ========================================
    
    async def flush(self) -> None:
        """刷新 Session（将更改写入数据库但不提交）"""
        await self._session.flush()
    
    async def refresh(self, entity: T) -> T:
        """刷新实体（从数据库重新加载）"""
        await self._session.refresh(entity)
        return entity


class NotFoundError(Exception):
    """
    实体不存在异常
    """
    def __init__(self, entity_type: str, entity_id: Any):
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(f"{entity_type} with id '{entity_id}' not found")


class DuplicateError(Exception):
    """
    实体已存在异常
    """
    def __init__(self, entity_type: str, entity_id: Any):
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(f"{entity_type} with id '{entity_id}' already exists")
