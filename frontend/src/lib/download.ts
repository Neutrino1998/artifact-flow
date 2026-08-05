const TEXT_ARTIFACT_EXTENSIONS: Record<string, string> = {
  'text/markdown': '.md',
  'text/plain': '.txt',
  'text/html': '.html',
  'text/css': '.css',
  'text/csv': '.csv',
  'application/json': '.json',
  'application/javascript': '.js',
  'text/javascript': '.js',
  'text/x-python': '.py',
  'text/x-typescript': '.ts',
};

export function getTextArtifactDownloadFilename(title: string, contentType: string): string {
  const safeTitle = title.replace(/[/\\?%*:|"<>]/g, '-');
  return safeTitle + (TEXT_ARTIFACT_EXTENSIONS[contentType] ?? '.txt');
}

/**
 * 触发浏览器把 Blob 存成文件 —— objectURL + 隐形 <a download> 点击的唯一实现
 * （此前散落 5 处 verbatim 拷贝）。
 *
 * revoke 刻意 **延后到下一拍**：`a.click()` 只是把下载任务排队，Firefox 会在任务
 * 真正读取 blob 之后才用到该 URL —— 同步 revoke 可能让下载静默失败。setTimeout(0)
 * 让出当前任务即可（下载一旦开始就持有 blob 引用，不需要更长的延迟）。
 */
export function triggerBlobDownload(filename: string, blob: Blob): void {
  triggerObjectUrlDownload(filename, URL.createObjectURL(blob));
}

/** 已持有 objectURL 时的变体（如 /raw 取回路径）。下载后代为 revoke（同样延后一拍）。 */
export function triggerObjectUrlDownload(filename: string, url: string): void {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
