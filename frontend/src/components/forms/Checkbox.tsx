'use client';

interface CheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  ariaLabel?: string;
  onClick?: (e: React.MouseEvent) => void;
}

// 用 stroked 路径 + 大幅占据 viewBox，比原 filled 路径更厚、更显眼
const CHECKMARK_SVG =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='white' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'><path d='M3 8.5l3.5 3.5L13 5'/></svg>\")";

export default function Checkbox({ checked, onChange, disabled, ariaLabel, onClick }: CheckboxProps) {
  return (
    <input
      type="checkbox"
      checked={checked}
      disabled={disabled}
      aria-label={ariaLabel}
      onChange={(e) => onChange(e.target.checked)}
      onClick={onClick}
      className="w-4 h-4 appearance-none rounded border border-border dark:border-border-dark bg-surface dark:bg-surface-dark checked:bg-accent checked:border-accent dark:checked:bg-accent dark:checked:border-accent cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0 transition-colors bg-center bg-no-repeat"
      style={checked ? { backgroundImage: CHECKMARK_SVG, backgroundSize: '100% 100%' } : undefined}
    />
  );
}
