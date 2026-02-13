'use client';

import { memo, useState, useEffect } from 'react';

function CyclingDots() {
  const [count, setCount] = useState(1);

  useEffect(() => {
    const id = setInterval(() => {
      setCount((c) => (c % 3) + 1);
    }, 400);
    return () => clearInterval(id);
  }, []);

  return <span>{'.'.repeat(count)}</span>;
}

export default memo(CyclingDots);
