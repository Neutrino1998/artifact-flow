'use client';

import { useState, useRef, useEffect, useCallback, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import DangerConfirmModal, { DangerConfirmTarget } from '@/components/layout/DangerConfirmModal';
import { BUTTON_GHOST_ICON, MENU_ROW_DANGER_HOVER } from '@/lib/styles';

// 会话行的 ⋮ 操作菜单(复制 ID / 删除对话)+ 删除确认弹窗。
// 侧栏 ConversationItem 与「搜索对话」ConversationBrowser 两处行内菜单原为复制粘贴,
// 这里收成单一实现(改一次两处都变,如之前 font-medium 要改两遍的坑)。
//
// 挂载方式(约束共同决定):
//   1) 触发器靠 hover 显露、并需 absolute+translate 定位在行内 → kebab 必须是行的
//      后代(相对行定位);
//   2) 下拉菜单必须脱离行内 DOM:侧栏列表和「搜索对话」列表都是 overflow-y-auto,
//      行内 absolute 会在列表底部被裁剪。菜单 createPortal 到 document.body,用触发器的
//      viewport 坐标 fixed 定位,并在底部空间不足时向上翻;列表滚动时直接关闭,避免
//      菜单滞后跟随或停留在已滚出视图的会话上。
//   3) 删除确认弹窗(DangerConfirmModal → DialogShell 的 `fixed inset-0`)若落在行内会被 kebab
//      wrapper 的 transform 祖先改掉定位基准(全屏遮罩错位)→ **createPortal 到 document.body**
//      脱离 DOM 子树修掉定位(fixed 按 DOM 祖先算)。
//      ⚠ portal 只搬 DOM,**不改 React 合成事件冒泡**(合成事件走 fiber 树,本组件仍是行
//      onClick 的 fiber 后代)—— 点弹窗遮罩(DialogShell backdrop 的 onClose 不 stopPropagation)
//      仍会冒到行 onClick 误触 onSelect/切会话。故 portal 内容再包一层 stopPropagation 的 div
//      在 fiber 边界拦掉(旧代码弹窗是行的**兄弟**、天然不冒到行,搬进子组件才有此坑)。
// 本组件由父级**无条件挂载**在行内(不放进 hover 门里,否则 hover 移开会连带卸载正打开的
// 弹窗):kebab 由 `visible` 自收显、包在 `wrapperClassName`(带 transform)里;弹窗走 portal。
//
// `open` 受控:父行仍需 menuOpen 驱动自身布局(行 z-40、标题 pr 让位),故开合留在父层。
// 删除实现两处不同(侧栏自持 API+store,浏览器委派父级刷新)→ 走 `onDelete` 注入。
interface Props {
  conversationId: string;
  title: string;
  // 是否显示 ⋮ 触发器 —— 父级的 hover/open 门控(= showMenu || menuOpen)。
  visible: boolean;
  // 下拉是否展开(受控)。
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDelete: (id: string) => void | Promise<void>;
  // Backend lease remains authoritative; this mirrors the active hint so the
  // common path does not offer an action that will be rejected with 409.
  deleteDisabled?: boolean;
  // kebab 在行内的绝对定位(两处 right-2 / right-3 不同,由父级传入)。
  wrapperClassName: string;
  // 触发器样式:两处所在表面不同(侧栏 accent 底 / 面板底),各传各的;
  // 默认用 surface-agnostic 的 BUTTON_GHOST_ICON。
  triggerClassName?: string;
}

const MENU_WIDTH = 160;
const MENU_GAP = 4;
const VIEWPORT_MARGIN = 8;
const FALLBACK_MENU_HEIGHT = 76;

export default function ConversationActionsMenu({
  conversationId,
  title,
  visible,
  open,
  onOpenChange,
  onDelete,
  deleteDisabled = false,
  wrapperClassName,
  triggerClassName = `${BUTTON_GHOST_ICON} p-1.5`,
}: Props) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [menuPosition, setMenuPosition] = useState<{ top: number; left: number } | null>(null);
  const { copy } = useCopyFeedback();
  const triggerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const updateMenuPosition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger || typeof window === 'undefined') return;

    const triggerRect = trigger.getBoundingClientRect();
    const menuHeight = dropdownRef.current?.offsetHeight || FALLBACK_MENU_HEIGHT;
    const maxLeft = Math.max(VIEWPORT_MARGIN, window.innerWidth - MENU_WIDTH - VIEWPORT_MARGIN);
    const left = Math.min(
      Math.max(triggerRect.right - MENU_WIDTH, VIEWPORT_MARGIN),
      maxLeft,
    );

    const belowTop = triggerRect.bottom + MENU_GAP;
    const aboveTop = triggerRect.top - menuHeight - MENU_GAP;
    const spaceBelow = window.innerHeight - triggerRect.bottom;
    const spaceAbove = triggerRect.top;
    const shouldFlipUp = spaceBelow < menuHeight + MENU_GAP && spaceAbove > spaceBelow;
    const rawTop = shouldFlipUp ? aboveTop : belowTop;
    const maxTop = Math.max(VIEWPORT_MARGIN, window.innerHeight - menuHeight - VIEWPORT_MARGIN);
    const top = Math.min(
      Math.max(rawTop, VIEWPORT_MARGIN),
      maxTop,
    );

    setMenuPosition({ top, left });
  }, []);

  // Close the dropdown on outside click (only armed while open).
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      const target = e.target as Node;
      if (
        !triggerRef.current?.contains(target) &&
        !dropdownRef.current?.contains(target)
      ) {
        onOpenChange(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (open && !visible) {
      onOpenChange(false);
    }
  }, [open, visible, onOpenChange]);

  useLayoutEffect(() => {
    if (!open || !visible) {
      setMenuPosition(null);
      return;
    }

    updateMenuPosition();

    const handleScroll = () => onOpenChange(false);

    window.addEventListener('resize', updateMenuPosition);
    window.addEventListener('scroll', handleScroll, true);
    return () => {
      window.removeEventListener('resize', updateMenuPosition);
      window.removeEventListener('scroll', handleScroll, true);
    };
  }, [open, visible, onOpenChange, updateMenuPosition]);

  const handleCopyId = async () => {
    await copy(conversationId);
    onOpenChange(false);
  };

  const handleConfirmDelete = async () => {
    await onDelete(conversationId);
    setConfirmDelete(false);
    onOpenChange(false);
  };

  return (
    <>
      {visible && (
        <div className={wrapperClassName}>
          <div ref={triggerRef} className="relative">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onOpenChange(!open);
              }}
              className={triggerClassName}
              aria-label="More actions"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                <circle cx="8" cy="3" r="1.5" />
                <circle cx="8" cy="8" r="1.5" />
                <circle cx="8" cy="13" r="1.5" />
              </svg>
            </button>
          </div>
        </div>
      )}

      {open && visible && typeof document !== 'undefined' && createPortal(
        <div
          ref={dropdownRef}
          className="fixed z-50 w-40 bg-surface dark:bg-panel-dark border border-border dark:border-border-dark rounded-lg shadow-modal p-1"
          style={{
            top: menuPosition?.top ?? -9999,
            left: menuPosition?.left ?? -9999,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            onClick={(e) => {
              e.stopPropagation();
              handleCopyId();
            }}
            className="w-full flex items-center gap-2 px-2.5 py-1.5 text-sm font-medium text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-surface-dark rounded-md transition-colors"
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="5" y="5" width="9" height="9" rx="1.5" />
              <path d="M5 11H3.5A1.5 1.5 0 0 1 2 9.5v-7A1.5 1.5 0 0 1 3.5 1h7A1.5 1.5 0 0 1 12 2.5V5" />
            </svg>
            复制 ID
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (deleteDisabled) return;
              onOpenChange(false);
              setConfirmDelete(true);
            }}
            disabled={deleteDisabled}
            title={deleteDisabled ? '任务运行中，完成或取消后才能删除' : undefined}
            className={`w-full flex items-center gap-2 px-2.5 py-1.5 text-sm font-medium text-status-error rounded-md ${
              deleteDisabled
                ? 'opacity-40 cursor-not-allowed'
                : MENU_ROW_DANGER_HOVER
            }`}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6" />
            </svg>
            删除对话
          </button>
        </div>,
        document.body,
      )}

      {confirmDelete && typeof document !== 'undefined' && createPortal(
        // stopPropagation 拦在 fiber 边界:portal 只搬 DOM,合成事件仍按 fiber 树冒到行
        // onClick(遮罩 onClose 不 stopPropagation),不拦会误触 onSelect/切会话。div 布局
        // 中性(DialogShell 根为 fixed inset-0,不占流)。
        <div onClick={(e) => e.stopPropagation()}>
          <DangerConfirmModal
            title="删除对话"
            message="操作不可恢复。"
            confirmLabel="确认删除"
            onConfirm={handleConfirmDelete}
            onCancel={() => setConfirmDelete(false)}
          >
            <DangerConfirmTarget name={title} />
          </DangerConfirmModal>
        </div>,
        document.body,
      )}
    </>
  );
}
