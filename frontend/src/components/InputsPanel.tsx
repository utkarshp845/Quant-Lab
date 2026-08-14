import type { BearPutSpreadFormState, OptionLegFormState, UnderlyingFormState } from "../types/form";

interface InputsPanelProps {
  form: BearPutSpreadFormState;
  onChange: (form: BearPutSpreadFormState) => void;
  fieldErrors?: string[];
}

function NumberField({
  label,
  value,
  onChange,
  step = "0.01",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  step?: string;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function UnderlyingFields({
  value,
  onChange,
}: {
  value: UnderlyingFormState;
  onChange: (v: UnderlyingFormState) => void;
}) {
  return (
    <div className="input-card underlying-card">
      <h3>Underlying</h3>
      <label className="field">
        <span className="field-label">Symbol</span>
        <input
          type="text"
          value={value.symbol}
          onChange={(e) => onChange({ ...value, symbol: e.target.value.toUpperCase() })}
        />
      </label>
      <NumberField
        label="Current Price ($)"
        value={value.price}
        onChange={(v) => onChange({ ...value, price: v })}
      />
      <NumberField
        label="Days to Expiration"
        value={value.dte}
        step="1"
        onChange={(v) => onChange({ ...value, dte: v })}
      />
    </div>
  );
}

function OptionLegFields({
  title,
  action,
  accentClass,
  value,
  onChange,
}: {
  title: string;
  action: "BUY" | "SELL";
  accentClass: string;
  value: OptionLegFormState;
  onChange: (v: OptionLegFormState) => void;
}) {
  return (
    <div className={`input-card option-card ${accentClass}`}>
      <h3>
        {title} <span className={`badge badge-${action.toLowerCase()}`}>{action}</span>
      </h3>
      <NumberField label="Strike ($)" value={value.strike} onChange={(v) => onChange({ ...value, strike: v })} />
      <NumberField label="Bid ($)" value={value.bid} onChange={(v) => onChange({ ...value, bid: v })} />
      <NumberField label="Ask ($)" value={value.ask} onChange={(v) => onChange({ ...value, ask: v })} />
      <NumberField
        label="Delta"
        value={value.delta}
        onChange={(v) => onChange({ ...value, delta: v })}
      />
      <NumberField
        label="IV (%)"
        value={value.ivPercent}
        step="0.1"
        onChange={(v) => onChange({ ...value, ivPercent: v })}
      />
    </div>
  );
}

export function InputsPanel({ form, onChange, fieldErrors }: InputsPanelProps) {
  return (
    <section className="section">
      <h2 className="section-title">1. Inputs</h2>
      <p className="section-subtitle">
        Enter the underlying and both option legs manually, exactly as you see them on your
        broker's option chain. Nothing here is fetched automatically.
      </p>
      <div className="inputs-grid">
        <UnderlyingFields
          value={form.underlying}
          onChange={(underlying) => onChange({ ...form, underlying })}
        />
        <OptionLegFields
          title="Long Put"
          action="BUY"
          accentClass="accent-buy"
          value={form.longPut}
          onChange={(longPut) => onChange({ ...form, longPut })}
        />
        <OptionLegFields
          title="Short Put"
          action="SELL"
          accentClass="accent-sell"
          value={form.shortPut}
          onChange={(shortPut) => onChange({ ...form, shortPut })}
        />
      </div>
      {fieldErrors && fieldErrors.length > 0 && (
        <div className="error-banner">
          <strong>Please fix the following:</strong>
          <ul>
            {fieldErrors.map((err) => (
              <li key={err}>{err}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
