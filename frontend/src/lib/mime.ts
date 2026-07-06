export const SAFE_INLINE_IMAGE_MIMES = new Set([
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
]);

export function isSafeInlineImageMime(contentType?: string | null) {
  const mime = (contentType ?? '').split(';', 1)[0].trim().toLowerCase();
  return SAFE_INLINE_IMAGE_MIMES.has(mime);
}
