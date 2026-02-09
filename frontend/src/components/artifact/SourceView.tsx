'use client';

import { memo } from 'react';

interface SourceViewProps {
  content: string;
}

function SourceView({ content }: SourceViewProps) {
  const lines = content.split('\n');

  return (
    <div className="p-4 font-mono text-xs">
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((line, i) => (
            <tr key={i} className="hover:bg-bg dark:hover:bg-bg-dark">
              <td className="pr-4 py-0.5 text-right select-none text-text-tertiary dark:text-text-tertiary-dark w-10">
                {i + 1}
              </td>
              <td className="py-0.5 text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-all">
                {line || '\u00A0'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default memo(SourceView);
