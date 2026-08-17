import { fmtIntOrDash, fmtNumberOrDash, fmtPercentOrDash } from "../../utils/researchFormat";

export type FeatureFieldFormat = "percent" | "number" | "int";

export interface FeatureField {
  label: string;
  value: number | null;
  format: FeatureFieldFormat;
}

function formatFeatureValue(value: number | null, format: FeatureFieldFormat): string {
  if (format === "percent") return fmtPercentOrDash(value, 2);
  if (format === "int") return fmtIntOrDash(value);
  return fmtNumberOrDash(value, 4);
}

/** One grouped card (Price/Volume/Volatility/Market Context/Price
 * Position) in the Feature Explorer -- reuses the existing `.section`/
 * `.section-title` card primitive and a `dl`-based label/value grid,
 * the same shape `.live-quote-card-grid`/`.historical-storage-status-grid`
 * already use elsewhere in this app. Every value came straight from
 * the backend's FeatureRecord -- nothing here is computed. */
export function FeatureGroupCard({
  title,
  subtitle,
  fields,
}: {
  title: string;
  subtitle?: string;
  fields: FeatureField[];
}) {
  return (
    <section className="section feature-group-card">
      <h3 className="section-title">{title}</h3>
      {subtitle && <p className="section-subtitle">{subtitle}</p>}
      <dl className="feature-group-grid">
        {fields.map((field) => (
          <div className="feature-group-row" key={field.label}>
            <dt>{field.label}</dt>
            <dd className={field.value === null ? "feature-value-null" : undefined}>
              {formatFeatureValue(field.value, field.format)}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
