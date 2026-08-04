'use client';

import { useEffect, useState, useRef } from 'react';
import { fetchWelcomeTips } from '@/lib/siteConfig';

const ROTATE_MS = 8000;
const FADE_MS = 300; // 单侧淡入/淡出时长,须与 tailwind tip-in/tip-out 动画时长一致
const FALLBACK = '开始对话，探索更多可能';

function displayTip(tip: string): string {
  return `TIPS：${tip}`;
}

/**
 * 欢迎页副标题：从 /site/welcome_tips.json 读字符串数组，每 8s 切换一条；hover 暂停；
 * 空列表 / fetch 失败回落到默认文案。
 *
 * 切换动画：单个节点「先淡出旧的 → 换文案 → 再淡入新的」的顺序过渡，全程只有一条 tip
 * 在场，故不会出现新旧两条交叉重叠(此前双层 absolute 交叉淡入淡出的问题)。
 */
export default function WelcomeTips() {
  const [tips, setTips] = useState<string[]>([]);
  const [idx, setIdx] = useState(0);
  const [phase, setPhase] = useState<'in' | 'out'>('in');
  const [paused, setPaused] = useState(false);
  const rotateRef = useRef<number | null>(null);
  const swapRef = useRef<number | null>(null);

  useEffect(() => {
    void fetchWelcomeTips().then(setTips);
  }, []);

  useEffect(() => {
    if (tips.length <= 1 || paused) {
      // 暂停(或不足两条)时清掉待执行的换文案定时器,并确保不停在「淡出」中间态
      // ——否则当前这条会停在 opacity:0 隐身。
      if (swapRef.current !== null) {
        window.clearTimeout(swapRef.current);
        swapRef.current = null;
      }
      setPhase('in');
      return;
    }
    rotateRef.current = window.setTimeout(() => {
      setPhase('out'); // 先淡出当前条
      swapRef.current = window.setTimeout(() => {
        setIdx((current) => (current + 1) % tips.length); // 淡出结束后换文案
        setPhase('in'); // 再淡入新的一条
      }, FADE_MS);
    }, ROTATE_MS);

    return () => {
      if (rotateRef.current !== null) window.clearTimeout(rotateRef.current);
      if (swapRef.current !== null) window.clearTimeout(swapRef.current);
    };
  }, [idx, tips.length, paused]);

  if (tips.length === 0) {
    return (
      <div className="text-text-tertiary dark:text-text-tertiary-dark">
        {displayTip(FALLBACK)}
      </div>
    );
  }

  if (tips.length === 1) {
    return (
      <div className="flex max-w-full items-center justify-center px-2 text-center text-text-tertiary dark:text-text-tertiary-dark">
        {displayTip(tips[0])}
      </div>
    );
  }

  return (
    <div
      className="flex min-h-6 w-full max-w-2xl items-center justify-center px-2 text-center text-text-tertiary dark:text-text-tertiary-dark"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      {/* key={idx} 让换文案时节点重挂载、重放 tip-in;仅切 phase 时(同 idx)靠
          animation-name 变化(tip-in↔tip-out)触发重播。 */}
      <span
        key={idx}
        className={`inline-block ${phase === 'out' ? 'animate-tip-out' : 'animate-tip-in'}`}
      >
        {displayTip(tips[idx])}
      </span>
    </div>
  );
}
