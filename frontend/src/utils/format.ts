export function fmtUsd(value: number, digits = 2): string {
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toFixed(digits)}`;
}

export function fmtSigned(value: number, digits = 2): string {
  const sign = value >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(value).toFixed(digits)}`;
}

export function fmtPercent(decimalValue: number, digits = 1): string {
  return `${(decimalValue * 100).toFixed(digits)}%`;
}

export function fmtNumber(value: number, digits = 2): string {
  return value.toFixed(digits);
}
