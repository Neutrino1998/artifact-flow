'use client';

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react';

interface ZoomableCanvasProps {
  contentWidth: number;
  contentHeight: number;
  children: ReactNode;
  resetKey?: string;
  onBackgroundClick?: () => void;
  ariaLabel?: string;
}

interface Point {
  x: number;
  y: number;
}

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 4;
const ZOOM_STEP = 0.25;
const FIT_PADDING = 48;

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

/**
 * Format-agnostic 2D viewport. `100%` means "fitted at natural size or smaller";
 * zoom is a multiplier over that stable baseline, so opening a very large image
 * is useful immediately while controls remain predictable across media types.
 */
export default function ZoomableCanvas({
  contentWidth,
  contentHeight,
  children,
  resetKey,
  onBackgroundClick,
  ariaLabel = 'Zoomable content',
}: ZoomableCanvasProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    pointerId: number;
    origin: Point;
    startPan: Point;
    moved: boolean;
  } | null>(null);
  const suppressBackgroundClickRef = useRef(false);
  const pointerDownOnBackgroundRef = useRef(false);
  const [viewport, setViewport] = useState({ width: 1, height: 1 });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);

  const safeWidth = Math.max(1, contentWidth);
  const safeHeight = Math.max(1, contentHeight);
  const fitScale = useMemo(() => {
    const availableWidth = Math.max(1, viewport.width - FIT_PADDING * 2);
    const availableHeight = Math.max(1, viewport.height - FIT_PADDING * 2);
    return Math.min(1, availableWidth / safeWidth, availableHeight / safeHeight);
  }, [safeHeight, safeWidth, viewport.height, viewport.width]);
  const effectiveScale = fitScale * zoom;
  const scaledWidth = safeWidth * effectiveScale;
  const scaledHeight = safeHeight * effectiveScale;
  const innerViewportWidth = Math.max(1, viewport.width - FIT_PADDING * 2);
  const innerViewportHeight = Math.max(1, viewport.height - FIT_PADDING * 2);
  const canPan = scaledWidth > innerViewportWidth || scaledHeight > innerViewportHeight;

  const clampPan = useCallback((next: Point): Point => {
    const maxX = Math.max(0, (scaledWidth - innerViewportWidth) / 2);
    const maxY = Math.max(0, (scaledHeight - innerViewportHeight) / 2);
    return {
      x: clamp(next.x, -maxX, maxX),
      y: clamp(next.y, -maxY, maxY),
    };
  }, [innerViewportHeight, innerViewportWidth, scaledHeight, scaledWidth]);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;

    const measure = () => {
      setViewport({
        width: element.clientWidth || window.innerWidth || 1,
        height: element.clientHeight || window.innerHeight || 1,
      });
    };
    measure();

    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(measure);
      observer.observe(element);
      return () => observer.disconnect();
    }
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);

  useEffect(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, [contentHeight, contentWidth, resetKey]);

  useEffect(() => {
    setPan((current) => clampPan(current));
  }, [clampPan]);

  const changeZoom = useCallback((delta: number) => {
    setZoom((current) => clamp(Number((current + delta).toFixed(2)), MIN_ZOOM, MAX_ZOOM));
  }, []);

  useEffect(() => {
    if (zoom === 1) setPan({ x: 0, y: 0 });
  }, [zoom]);

  const reset = useCallback(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      changeZoom(event.deltaY < 0 ? 0.1 : -0.1);
    };
    element.addEventListener('wheel', handleWheel, { passive: false });
    return () => element.removeEventListener('wheel', handleWheel);
  }, [changeZoom]);

  const handlePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    // Pointer capture retargets the eventual click to the capturing viewport,
    // even when the original press was on the image/SVG and capture has already
    // been released at pointerup. Preserve the pre-capture hit-test result so a
    // content click cannot be mistaken for a background click later.
    pointerDownOnBackgroundRef.current = event.target === event.currentTarget;
    if (!canPan) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      origin: { x: event.clientX, y: event.clientY },
      startPan: pan,
      moved: false,
    };
    setDragging(true);
  }, [canPan, pan]);

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.origin.x;
    const dy = event.clientY - drag.origin.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) drag.moved = true;
    setPan(clampPan({ x: drag.startPan.x + dx, y: drag.startPan.y + dy }));
  }, [clampPan]);

  const finishDrag = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) {
      if (event.type === 'pointercancel') {
        pointerDownOnBackgroundRef.current = false;
        suppressBackgroundClickRef.current = false;
      }
      return;
    }
    suppressBackgroundClickRef.current = drag.moved;
    if (event.type === 'pointercancel') {
      pointerDownOnBackgroundRef.current = false;
      suppressBackgroundClickRef.current = false;
    }
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, []);

  const handleKeyDown = useCallback((event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === '+' || event.key === '=') {
      event.preventDefault();
      changeZoom(ZOOM_STEP);
    } else if (event.key === '-') {
      event.preventDefault();
      changeZoom(-ZOOM_STEP);
    } else if (event.key === '0') {
      event.preventDefault();
      reset();
    }
  }, [changeZoom, reset]);

  return (
    <div
      ref={viewportRef}
      tabIndex={0}
      aria-label={ariaLabel}
      className={`relative h-full w-full touch-none overflow-hidden outline-none ${
        canPan ? (dragging ? 'cursor-grabbing' : 'cursor-grab') : 'cursor-default'
      }`}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={finishDrag}
      onPointerCancel={finishDrag}
      onKeyDown={handleKeyDown}
      onDoubleClick={reset}
      onClick={(event) => {
        const startedOnBackground = pointerDownOnBackgroundRef.current;
        pointerDownOnBackgroundRef.current = false;
        if (suppressBackgroundClickRef.current) {
          suppressBackgroundClickRef.current = false;
          return;
        }
        if (startedOnBackground && event.target === event.currentTarget) {
          onBackgroundClick?.();
        }
      }}
    >
      <div
        className="pointer-events-auto absolute left-1/2 top-1/2 select-none"
        style={{
          width: safeWidth,
          height: safeHeight,
          transform: `translate(-50%, -50%) translate(${pan.x}px, ${pan.y}px) scale(${effectiveScale})`,
          transformOrigin: 'center',
        }}
      >
        {children}
      </div>

      <div
        className="absolute bottom-5 left-1/2 z-10 flex -translate-x-1/2 items-center rounded-full border border-border/70 dark:border-border-dark bg-surface/90 dark:bg-surface-dark/90 p-1 shadow-float"
        onPointerDown={(event) => event.stopPropagation()}
        // A rapid pair of button clicks also emits `dblclick`. Keep that event
        // inside the controls; otherwise it reaches the canvas-level reset
        // handler and makes +/+ or -/- appear to jump unpredictably to 100%.
        onDoubleClick={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          onClick={() => changeZoom(-ZOOM_STEP)}
          disabled={zoom <= MIN_ZOOM}
          className="flex h-10 w-10 items-center justify-center rounded-full text-xl text-text-secondary dark:text-text-secondary-dark hover:bg-text-primary/5 dark:hover:bg-text-primary-dark/10 hover:text-text-primary dark:hover:text-text-primary-dark disabled:opacity-30"
          aria-label="Zoom out"
          title="缩小"
        >
          −
        </button>
        <button
          type="button"
          onClick={reset}
          className="min-w-20 px-3 text-sm font-medium tabular-nums text-text-primary dark:text-text-primary-dark"
          aria-label="Reset zoom"
          title="适应窗口"
        >
          {Math.round(zoom * 100)}%
        </button>
        <button
          type="button"
          onClick={() => changeZoom(ZOOM_STEP)}
          disabled={zoom >= MAX_ZOOM}
          className="flex h-10 w-10 items-center justify-center rounded-full text-xl text-text-secondary dark:text-text-secondary-dark hover:bg-text-primary/5 dark:hover:bg-text-primary-dark/10 hover:text-text-primary dark:hover:text-text-primary-dark disabled:opacity-30"
          aria-label="Zoom in"
          title="放大"
        >
          +
        </button>
      </div>
    </div>
  );
}
