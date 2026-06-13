import { describe, test, expect } from 'vitest';
import { partitionStageable } from './uploadFilter';

// 上传翻转后(2026-06-11)后端收任意格式,前端唯一可预判的闸 = 单文件体积。
// 原扩展名黑名单镜像(rejectionReason)已随后端拒绝名单一起删除。

describe('uploadFilter.partitionStageable', () => {
  test('all formats pass — no extension blacklist anymore', () => {
    const { accepted, rejected } = partitionStageable([
      new File(['x'], 'a.txt'),
      new File(['x'], 'b.doc'),
      new File(['x'], 'c.xlsx'),
      new File(['x'], 'd.ods'),
      new File(['x'], 'e.gif'),
      new File(['x'], 'f.zip'),
      new File(['x'], 'g.bin'),
    ]);
    expect(accepted.map((f) => f.name)).toEqual([
      'a.txt', 'b.doc', 'c.xlsx', 'd.ods', 'e.gif', 'f.zip', 'g.bin',
    ]);
    expect(rejected).toEqual([]);
  });

  test('rejects files over maxBytes with a size reason; under-limit pass', () => {
    const small = new File(['ab'], 'small.txt'); // 2 bytes
    const big = new File(['abcdef'], 'big.txt'); // 6 bytes
    const { accepted, rejected } = partitionStageable([small, big], 4);
    expect(accepted.map((f) => f.name)).toEqual(['small.txt']);
    expect(rejected.map((r) => r.name)).toEqual(['big.txt']);
    expect(rejected[0].reason).toContain('文件过大');
  });

  test('maxBytes omitted → size gate skipped (limit not yet fetched)', () => {
    const big = new File(['abcdef'], 'big.txt');
    const { accepted } = partitionStageable([big]);
    expect(accepted.map((f) => f.name)).toEqual(['big.txt']);
  });
});
