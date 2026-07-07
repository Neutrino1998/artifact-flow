export interface CsvPreviewResult {
  rows: string[][];
  truncatedRows: boolean;
  truncatedColumns: boolean;
  maxColumnsSeen: number;
}

export interface CsvPreviewOptions {
  maxRows: number;
  maxColumns: number;
}

export function parseCsvPreview(
  input: string,
  { maxRows, maxColumns }: CsvPreviewOptions
): CsvPreviewResult {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let inQuotes = false;
  let maxColumnsSeen = 0;
  let truncatedRows = false;
  let truncatedColumns = false;

  const pushRow = () => {
    row.push(cell);
    cell = '';
    maxColumnsSeen = Math.max(maxColumnsSeen, row.length);
    if (row.length > maxColumns) {
      truncatedColumns = true;
    }
    if (rows.length < maxRows) {
      rows.push(row.slice(0, maxColumns));
    } else {
      truncatedRows = true;
    }
    row = [];
  };

  for (let i = 0; i < input.length; i += 1) {
    const ch = input[i];

    if (inQuotes) {
      if (ch === '"') {
        if (input[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        cell += ch;
      }
      continue;
    }

    if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      row.push(cell);
      cell = '';
    } else if (ch === '\n') {
      pushRow();
    } else if (ch === '\r') {
      if (input[i + 1] === '\n') i += 1;
      pushRow();
    } else {
      cell += ch;
    }

    if (truncatedRows && rows.length >= maxRows) {
      break;
    }
  }

  if (!truncatedRows && (cell.length > 0 || row.length > 0 || input.length === 0)) {
    pushRow();
  }

  return { rows, truncatedRows, truncatedColumns, maxColumnsSeen };
}
