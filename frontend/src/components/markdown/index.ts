import type { Components } from 'react-markdown';
import CodeBlock from './CodeBlock';

export const markdownComponents: Partial<Components> = {
  pre: CodeBlock as Components['pre'],
};
