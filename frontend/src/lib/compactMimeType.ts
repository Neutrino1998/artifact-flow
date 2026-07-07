export function compactMimeType(mimeType: string, maxLength = 36): string {
  const value = mimeType.trim();
  if (value.length <= maxLength) return value;
  if (maxLength <= 3) return value.slice(0, maxLength);

  const available = maxLength - 3;
  const headLength = Math.ceil(available * 0.6);
  const tailLength = available - headLength;

  return `${value.slice(0, headLength)}...${value.slice(-tailLength)}`;
}
