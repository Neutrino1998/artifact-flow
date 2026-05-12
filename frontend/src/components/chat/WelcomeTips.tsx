'use client';

import { useEffect, useState, useRef } from 'react';
import { fetchWelcomeTips } from '@/lib/siteConfig';

const ROTATE_MS = 8000;
const ANIM_MS = 500;
const FALLBACK = '开始对话，探索更多可能';

function LightbulbIcon({ className = '' }: { className?: string }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M8 1.5a4.5 4.5 0 0 0-2.7 8.1c.4.3.7.8.7 1.4v.5h4v-.5c0-.6.3-1.1.7-1.4A4.5 4.5 0 0 0 8 1.5z" />
      <path d="M6 13h4M6.5 14.5h3" />
    </svg>
  );
}

/**
 * 欢迎页副标题：从 /site/welcome_tips.json 读字符串数组，
 * 每 5s 向左滑动切换一条；hover 暂停；空列表 / fetch 失败回落到默认文案。
 */
export default function WelcomeTips() {
  const [tips, setTips] = useState<string[]>([]);
  const [idx, setIdx] = useState(0);
  const [prevIdx, setPrevIdx] = useState<number | null>(null);
  const [paused, setPaused] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    void fetchWelcomeTips().then(setTips);
  }, []);

  useEffect(() => {
    if (tips.length <= 1 || paused) return;
    timerRef.current = window.setTimeout(() => {
      setPrevIdx(idx);
      setIdx((current) => (current + 1) % tips.length);
      // 清除上一条（动画结束后），避免堆叠多个 DOM 节点
      window.setTimeout(() => setPrevIdx(null), ANIM_MS);
    }, ROTATE_MS);

    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [idx, tips.length, paused]);

  if (tips.length === 0) {
    return (
      <div className="text-text-tertiary dark:text-text-tertiary-dark">
        {FALLBACK}
      </div>
    );
  }

  if (tips.length === 1) {
    return (
      <div className="flex items-center justify-center text-text-tertiary dark:text-text-tertiary-dark">
        <span className="relative">
          <LightbulbIcon className="absolute right-full top-1/2 -translate-y-1/2 mr-2 shrink-0" />
          {tips[0]}
        </span>
      </div>
    );
  }

  return (
    <div
      className="relative overflow-hidden h-6 w-full max-w-2xl text-text-tertiary dark:text-text-tertiary-dark"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      {prevIdx !== null && (
        <div
          key={`out-${prevIdx}`}
          className="absolute inset-0 flex items-center justify-center animate-slide-out-left px-4"
        >
          <span className="relative">
            <LightbulbIcon className="absolute right-full top-1/2 -translate-y-1/2 mr-2 shrink-0" />
            {tips[prevIdx]}
          </span>
        </div>
      )}
      <div
        key={`in-${idx}`}
        className="absolute inset-0 flex items-center justify-center animate-slide-in-right px-4"
      >
        <span className="relative">
          <LightbulbIcon className="absolute right-full top-1/2 -translate-y-1/2 mr-2 shrink-0" />
          {tips[idx]}
        </span>
      </div>
    </div>
  );
}
