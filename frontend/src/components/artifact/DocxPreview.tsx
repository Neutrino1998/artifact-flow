'use client';

import { useEffect, useRef, useState } from 'react';
import { fetchArtifactRawBlob } from '@/lib/api';
import { useArtifactStore } from '@/stores/artifactStore';
import BinaryFilePreview from './BinaryFilePreview';

const MAX_PREVIEW_BYTES = 15 * 1024 * 1024;
const FRAME_SRC_DOC = '<!doctype html><html><head><base target="_blank"></head><body></body></html>';
const FRAME_STYLE_ID = 'artifact-docx-preview-style';
const PAGE_GUTTER_PX = 32;
const FALLBACK_PAGE_WIDTH_PX = 794;
const FALLBACK_PAGE_ASPECT = 1.414;
const PAGE_OVERFLOW_TOLERANCE_PX = 2;
const WORDPROCESSINGML_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';

function installFrameStyles(doc: Document) {
  doc.getElementById(FRAME_STYLE_ID)?.remove();
  const style = doc.createElement('style');
  style.id = FRAME_STYLE_ID;
  style.textContent = `
    html, body {
      margin: 0;
      min-height: 100%;
      background: #f3f4f6;
      color: #111827;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    body {
      box-sizing: border-box;
    }
    a {
      color: inherit;
      text-decoration: underline;
    }
    .docx-wrapper {
      background: transparent !important;
      box-sizing: border-box !important;
      display: block !important;
      min-height: 100% !important;
      padding: 16px !important;
    }
    .artifact-docx-page-frame {
      margin: 0 auto 16px auto !important;
      transform-origin: top left;
    }
    .artifact-docx-page-frame > section.docx,
    .docx-wrapper > section.docx {
      background: #fff !important;
      box-shadow: 0 8px 28px rgba(15, 23, 42, 0.16) !important;
      max-width: none !important;
      position: relative !important;
      transform-origin: top left;
    }
    .artifact-docx-page-frame > section.docx {
      margin: 0 !important;
    }
    .docx-wrapper > section.docx {
      margin: 0 auto 16px auto !important;
    }
    .docx-wrapper ins {
      background: rgba(254, 202, 202, 0.35);
      color: #b91c1c;
      text-decoration: underline;
      text-decoration-color: #dc2626;
      text-underline-offset: 0.12em;
    }
    .docx-wrapper del {
      background: rgba(254, 202, 202, 0.28);
      color: #991b1b;
      text-decoration-color: #dc2626;
    }
    .docx-comment-ref {
      background: #fef3c7;
      border: 1px solid #f59e0b;
      border-radius: 999px;
      color: #92400e;
      display: inline-flex;
      font: 11px/1 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0 2px;
      padding: 1px 4px;
      vertical-align: text-top;
    }
    .docx-comment-popover {
      border: 1px solid rgba(15, 23, 42, 0.14);
      border-radius: 6px;
      box-shadow: 0 12px 28px rgba(15, 23, 42, 0.18) !important;
      color: #111827 !important;
      font: 12px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
      max-height: 260px;
      overflow: auto;
      width: 280px !important;
      white-space: normal;
    }
    .docx-comment-popover * {
      color: inherit !important;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
      font-size: 12px !important;
      line-height: 1.45 !important;
    }
    .docx-comment-author,
    .docx-comment-date {
      color: #64748b !important;
      font-size: 11px !important;
    }
    .artifact-docx-page-number {
      bottom: 18px;
      color: #111827;
      font: 12px/1 Georgia, "Times New Roman", serif;
      left: 0;
      opacity: 0.75;
      pointer-events: none;
      position: absolute;
      right: 0;
      text-align: center;
      z-index: 2;
    }
  `;
  doc.head.appendChild(style);
}

function resetFrame(doc: Document) {
  doc.head.innerHTML = `
    <base target="_blank">
  `;
  installFrameStyles(doc);
  doc.body.innerHTML = '';
}

function readElementWidth(win: Window, element: HTMLElement) {
  if (element.offsetWidth > 0) return element.offsetWidth;

  const computedWidth = Number.parseFloat(win.getComputedStyle(element).width);
  if (Number.isFinite(computedWidth) && computedWidth > 0) return computedWidth;

  return element.offsetWidth || FALLBACK_PAGE_WIDTH_PX;
}

function readCssLengthPx(value: string | null | undefined) {
  if (!value || value === 'auto') return null;

  const numeric = Number.parseFloat(value);
  if (!Number.isFinite(numeric)) return null;

  if (value.endsWith('pt')) return numeric * (96 / 72);
  if (value.endsWith('in')) return numeric * 96;
  if (value.endsWith('cm')) return numeric * (96 / 2.54);
  if (value.endsWith('mm')) return numeric * (96 / 25.4);
  return numeric;
}

function readElementHeight(win: Window, element: HTMLElement) {
  const inlineHeight = readCssLengthPx(element.style.height);
  if (inlineHeight && inlineHeight > 0) return inlineHeight;

  const inlineMinHeight = readCssLengthPx(element.style.minHeight);
  if (inlineMinHeight && inlineMinHeight > 0) return inlineMinHeight;

  const computed = win.getComputedStyle(element);
  const computedMinHeight = readCssLengthPx(computed.minHeight);
  if (computedMinHeight && computedMinHeight > 0) return computedMinHeight;

  const computedHeight = readCssLengthPx(computed.height);
  if (computedHeight && computedHeight > 0) return computedHeight;

  return readElementWidth(win, element) * FALLBACK_PAGE_ASPECT;
}

function directArticle(section: HTMLElement) {
  return Array.from(section.children).find(
    (child): child is HTMLElement => child.nodeType === 1 && child.tagName === 'ARTICLE'
  ) ?? null;
}

function hasArticleContent(section: HTMLElement) {
  const article = directArticle(section);
  if (!article) return false;
  const text = (article.textContent ?? '').replace(/\s+/g, '');
  return text.length > 0 || !!article.querySelector('img, svg, table, canvas, object, embed');
}

function removeGeneratedLeadingEmptyPages(doc: Document) {
  const sections = Array.from(doc.querySelectorAll('.docx-wrapper > section.docx')) as HTMLElement[];
  for (const section of sections.slice(0, -1)) {
    if (hasArticleContent(section)) break;
    section.remove();
  }
}

function fixPageHeight(section: HTMLElement, pageHeight: number) {
  section.style.height = `${pageHeight}px`;
  section.style.minHeight = `${pageHeight}px`;
  section.style.overflow = 'hidden';
}

function paginateSection(win: Window, section: HTMLElement) {
  const article = directArticle(section);
  if (!article) return;

  const nodes = Array.from(article.childNodes);
  if (nodes.length === 0) return;

  const computedSection = win.getComputedStyle(section);
  const verticalPadding =
    (readCssLengthPx(computedSection.paddingTop) ?? 0) +
    (readCssLengthPx(computedSection.paddingBottom) ?? 0);
  const pageHeight = readElementHeight(win, section);
  const pageContentHeight = Math.max(1, pageHeight - verticalPadding);
  const originalArticle = article.cloneNode(false) as HTMLElement;

  fixPageHeight(section, pageHeight);
  article.replaceChildren();

  let currentPage = section;
  let currentArticle = article;
  let nextNodeNeedsNewPage = false;

  const appendPage = () => {
    const page = section.cloneNode(false) as HTMLElement;
    const pageArticle = originalArticle.cloneNode(false) as HTMLElement;
    fixPageHeight(page, pageHeight);
    page.appendChild(pageArticle);
    currentPage.after(page);
    currentPage = page;
    currentArticle = pageArticle;
  };

  const growCurrentPageIfNeeded = () => {
    const requiredHeight = currentArticle.scrollHeight + verticalPadding;
    if (requiredHeight > pageHeight + PAGE_OVERFLOW_TOLERANCE_PX) {
      fixPageHeight(currentPage, requiredHeight);
    }
  };

  for (const node of nodes) {
    if (nextNodeNeedsNewPage) {
      appendPage();
      nextNodeNeedsNewPage = false;
    }

    currentArticle.appendChild(node);

    if (currentArticle.scrollHeight <= pageContentHeight + PAGE_OVERFLOW_TOLERANCE_PX) {
      continue;
    }

    if (currentArticle.childNodes.length > 1) {
      currentArticle.removeChild(node);
      appendPage();
      currentArticle.appendChild(node);
    }

    if (currentArticle.scrollHeight > pageContentHeight + PAGE_OVERFLOW_TOLERANCE_PX) {
      growCurrentPageIfNeeded();
      nextNodeNeedsNewPage = true;
    }
  }
}

function paginateRenderedSections(doc: Document) {
  const win = doc.defaultView;
  if (!win) return;

  removeGeneratedLeadingEmptyPages(doc);

  const sections = Array.from(doc.querySelectorAll('.docx-wrapper > section.docx')) as HTMLElement[];
  for (const section of sections) {
    paginateSection(win, section);
  }
}

function addGeneratedPageNumbers(doc: Document) {
  const pages = Array.from(doc.querySelectorAll('.docx-wrapper > section.docx')) as HTMLElement[];
  pages.forEach((page, index) => {
    for (const child of Array.from(page.children)) {
      if (child.classList.contains('artifact-docx-page-number')) {
        child.remove();
      }
    }

    const pageNumber = doc.createElement('div');
    pageNumber.className = 'artifact-docx-page-number';
    pageNumber.setAttribute('aria-hidden', 'true');
    pageNumber.textContent = String(index + 1);
    page.appendChild(pageNumber);
  });
}

function hasParagraphSectionProperties(paragraph: Element) {
  return Array.from(paragraph.children).some(
    (child) =>
      child.localName === 'pPr' &&
      Array.from(child.children).some((grandchild) => grandchild.localName === 'sectPr')
  );
}

function hasPageBreakMarker(xmlDoc: Document) {
  const breaks = Array.from(xmlDoc.getElementsByTagNameNS(WORDPROCESSINGML_NS, 'br'));
  const hasManualBreak = breaks.some(
    (node) => (node.getAttributeNS(WORDPROCESSINGML_NS, 'type') ?? node.getAttribute('w:type')) === 'page'
  );
  if (hasManualBreak) return true;

  if (xmlDoc.getElementsByTagNameNS(WORDPROCESSINGML_NS, 'lastRenderedPageBreak').length > 0) return true;
  if (xmlDoc.getElementsByTagNameNS(WORDPROCESSINGML_NS, 'pageBreakBefore').length > 0) return true;

  const paragraphs = Array.from(xmlDoc.getElementsByTagNameNS(WORDPROCESSINGML_NS, 'p'));
  return paragraphs.some(hasParagraphSectionProperties);
}

async function hasDocumentSuppliedPagination(blob: Blob) {
  try {
    const { default: JSZip } = await import('jszip');
    const zip = await JSZip.loadAsync(blob);
    const documentXml = await zip.file('word/document.xml')?.async('string');
    if (!documentXml) return true;

    const xmlDoc = new DOMParser().parseFromString(documentXml, 'application/xml');
    if (xmlDoc.getElementsByTagName('parsererror').length > 0) return true;

    return hasPageBreakMarker(xmlDoc);
  } catch {
    return true;
  }
}

function prepareFixedPageLayout(doc: Document) {
  const win = doc.defaultView;
  if (!win) return () => {};

  const pages = Array.from(doc.querySelectorAll('.docx-wrapper > section.docx')) as HTMLElement[];
  const pageFrames = pages.map((page) => {
    const frame = doc.createElement('div');
    frame.className = 'artifact-docx-page-frame';
    page.before(frame);
    frame.appendChild(page);
    return { frame, page };
  });

  let frameId = 0;
  const applyScale = () => {
    frameId = 0;
    const availableWidth = Math.max(1, doc.documentElement.clientWidth - PAGE_GUTTER_PX);
    const maxPageWidth = Math.max(
      FALLBACK_PAGE_WIDTH_PX,
      ...pageFrames.map(({ page }) => readElementWidth(win, page))
    );
    const scale = Math.min(1, availableWidth / maxPageWidth);

    for (const { frame, page } of pageFrames) {
      const pageWidth = readElementWidth(win, page);
      const pageHeight = page.offsetHeight || page.scrollHeight;
      page.style.transform = `scale(${scale})`;
      frame.style.width = `${pageWidth * scale}px`;
      frame.style.height = `${pageHeight * scale}px`;
    }
  };
  const scheduleApplyScale = () => {
    if (frameId) win.cancelAnimationFrame(frameId);
    frameId = win.requestAnimationFrame(applyScale);
  };

  scheduleApplyScale();

  let resizeObserver: ResizeObserver | null = null;
  if (win.ResizeObserver) {
    resizeObserver = new win.ResizeObserver(scheduleApplyScale);
    resizeObserver.observe(doc.documentElement);
  } else {
    win.addEventListener('resize', scheduleApplyScale);
  }

  const images = Array.from(doc.querySelectorAll('img'));
  for (const image of images) {
    image.addEventListener('load', scheduleApplyScale);
  }

  return () => {
    if (frameId) win.cancelAnimationFrame(frameId);
    resizeObserver?.disconnect();
    win.removeEventListener('resize', scheduleApplyScale);
    for (const image of images) {
      image.removeEventListener('load', scheduleApplyScale);
    }
  };
}

export default function DocxPreview({
  sessionId,
  artifactId,
  originalFilename,
  contentType,
}: {
  sessionId: string;
  artifactId: string;
  originalFilename?: string | null;
  contentType: string;
}) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const pendingFlush = useArtifactStore((s) => !!s.liveContent[artifactId]);
  const [frameReady, setFrameReady] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (pendingFlush || frameReady === 0) return;
    let cancelled = false;
    let cleanupLayout: (() => void) | null = null;
    setLoading(true);
    setError(null);

    fetchArtifactRawBlob(sessionId, artifactId)
      .then(async (blob) => {
        if (blob.size > MAX_PREVIEW_BYTES) {
          throw new Error('Word 文件较大，已跳过浏览器内预览');
        }

        const doc = iframeRef.current?.contentDocument;
        if (!doc) throw new Error('Word 预览容器未就绪');
        resetFrame(doc);

        const hasNativePagination = await hasDocumentSuppliedPagination(blob);
        const { renderAsync } = await import('docx-preview');
        await renderAsync(blob, doc.body, doc.head, {
          // docx-preview cannot calculate Word's natural page breaks. Keep the
          // document's fixed page width and scale the page shell instead of
          // reflowing text when the artifact panel is resized.
          breakPages: hasNativePagination,
          ignoreHeight: false,
          ignoreLastRenderedPageBreak: false,
          ignoreWidth: false,
          renderHeaders: hasNativePagination,
          renderFooters: hasNativePagination,
          renderComments: true,
          renderChanges: true,
          renderAltChunks: false,
          renderFootnotes: true,
          renderEndnotes: true,
          useBase64URL: true,
        });
        if (cancelled) return;
        if (!hasNativePagination) {
          paginateRenderedSections(doc);
          addGeneratedPageNumbers(doc);
        }
        installFrameStyles(doc);
        cleanupLayout = prepareFixedPageLayout(doc);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Word 预览失败，可下载原件查看');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      cleanupLayout?.();
    };
  }, [artifactId, frameReady, pendingFlush, sessionId]);

  if (pendingFlush || error) {
    return (
      <BinaryFilePreview
        sessionId={sessionId}
        artifactId={artifactId}
        originalFilename={originalFilename}
        contentType={contentType}
        description={error ?? 'Word 原件将在本回合完成后可预览'}
        pendingMessage="本回合完成后可预览或下载原件"
      />
    );
  }

  return (
    <div className="relative h-full bg-bg dark:bg-bg-dark">
      <iframe
        ref={iframeRef}
        title="Word preview"
        sandbox="allow-same-origin"
        referrerPolicy="no-referrer"
        srcDoc={FRAME_SRC_DOC}
        onLoad={() => setFrameReady((n) => n + 1)}
        className="block h-full w-full border-0 bg-white"
      />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-chat/80 text-sm text-text-tertiary backdrop-blur-sm dark:bg-chat-dark/80 dark:text-text-tertiary-dark">
          正在渲染 Word...
        </div>
      )}
    </div>
  );
}
