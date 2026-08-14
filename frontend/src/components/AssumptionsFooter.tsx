const ASSUMPTIONS = [
  "This is an educational research tool. It does not provide financial advice.",
  "Calculations ignore commissions and fees unless explicitly added later.",
  "The expected-move calculation is a simplified approximation.",
  "The probability calculation assumes a simplified normal distribution.",
  "The probability estimate is NOT equivalent to the exact probability implied by the option chain.",
  "The tool does not account for volatility skew/smile yet.",
  "The tool does not model early exercise or assignment yet.",
  "The tool does not execute trades.",
  "The tool does not connect to a brokerage.",
  "The tool does not make autonomous trading decisions.",
];

/** Spec section 21: these assumptions must be visible somewhere accessible in the app. */
export function AssumptionsFooter() {
  return (
    <footer className="assumptions-footer">
      <h3>Important Assumptions</h3>
      <ul>
        {ASSUMPTIONS.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
    </footer>
  );
}
