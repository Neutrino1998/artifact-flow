'use client';

import { useNotificationConfigStore } from '@/stores/notificationConfigStore';
import { MENU_ROW_HOVER } from '@/lib/styles';

export default function NotificationConfigList() {
  const items = useNotificationConfigStore((s) => s.items);
  const selectedIndex = useNotificationConfigStore((s) => s.selectedIndex);
  const loading = useNotificationConfigStore((s) => s.loading);
  const setSelectedIndex = useNotificationConfigStore((s) => s.setSelectedIndex);

  return (
    <>
      <div className="px-5 pt-2 pb-1 flex items-center justify-between gap-3 text-xs font-semibold text-text-tertiary dark:text-text-tertiary-dark">
        <span>通知列表</span>
        <span className="font-normal tabular-nums">{items.length}/50</span>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading && items.length === 0 ? (
          <div className="px-4 py-8 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
            加载中...
          </div>
        ) : items.length === 0 ? (
          <div className="px-4 py-8 text-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
            暂无通知
          </div>
        ) : (
          items.map((item, index) => {
            const isActive = index === selectedIndex;
            return (
              <div key={item.id} className="mx-2 mb-1">
                <button
                  type="button"
                  onClick={() => setSelectedIndex(index)}
                  className={`group relative w-full min-w-0 text-left cursor-pointer transition-colors rounded-lg px-3 py-2.5 overflow-hidden ${
                    isActive
                      ? 'bg-chat dark:bg-panel-accent-dark'
                      : MENU_ROW_HOVER
                  }`}
                >
                  {isActive && (
                    <span
                      aria-hidden="true"
                      className="absolute left-0 top-2 bottom-2 w-0.5 rounded-r-full bg-accent"
                    />
                  )}
                  <div className="flex items-center min-w-0 overflow-hidden font-medium text-text-primary dark:text-text-primary-dark">
                    <span className="block min-w-0 flex-1 truncate">{item.title || '未命名通知'}</span>
                  </div>
                  <div className="mt-0.5 min-w-0 overflow-hidden text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
                    {item.id}
                  </div>
                </button>
              </div>
            );
          })
        )}
      </div>
    </>
  );
}
