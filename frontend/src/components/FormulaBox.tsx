interface FormulaBoxProps {
  formula?: string;
  substitution?: string;
  result?: string;
}

/**
 * Renders "formula -> substituted values -> result" as a small
 * monospace block. This is the recurring visual device that makes the
 * math inspectable instead of hidden behind a single output number.
 * `formula` may be omitted when a box is only showing a substitution
 * line (e.g. a second worked example right below one that already
 * stated the formula).
 */
export function FormulaBox({ formula, substitution, result }: FormulaBoxProps) {
  return (
    <div className="formula-box">
      {formula && <div className="formula-line formula-definition">{formula}</div>}
      {substitution && <div className="formula-line formula-substitution">{substitution}</div>}
      {result && <div className="formula-line formula-result">= {result}</div>}
    </div>
  );
}
