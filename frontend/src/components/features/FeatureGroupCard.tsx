import { fmtIntOrDash, fmtNumberOrDash, fmtPercentOrDash } from "../../utils/researchFormat";

export type FeatureFieldFormat = "percent" | "number" | "int";

export interface FeatureField {
  label: string;
  value: number | null;
  format: FeatureFieldFormat;
  /** The canonical feature_id (app/features/vocabulary.py) this field
   * mirrors -- optional (Market Context's None-symbol case has no
   * fields at all); when present, enables the RESEARCH column (spec
   * section 13: "whether usable by Research, experiments currently
   * using it") and the "Use this feature in Research" action. */
  featureId?: string;
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
  experimentsUsingFeature,
  onUseInResearch,
}: {
  title: string;
  subtitle?: string;
  fields: FeatureField[];
  /** feature_id -> count of experiments referencing it (spec section
   * 13's RESEARCH column) -- omitted fields default to 0/unused. */
  experimentsUsingFeature?: Record<string, number>;
  onUseInResearch?: (featureId: string) => void;
}) {
  return (
    <section className="section feature-group-card">
      <h3 className="section-title">{title}</h3>
      {subtitle && <p className="section-subtitle">{subtitle}</p>}
      <dl className="feature-group-grid">
        {fields.map((field) => {
          const usageCount = field.featureId ? (experimentsUsingFeature?.[field.featureId] ?? 0) : null;
          return (
            <div className="feature-group-row" key={field.label}>
              <dt>{field.label}</dt>
              <dd className={field.value === null ? "feature-value-null" : undefined}>
                {formatFeatureValue(field.value, field.format)}
              </dd>
              {field.featureId && onUseInResearch && (
                <dd className="feature-group-research-cell">
                  {usageCount !== null && usageCount > 0 && (
                    <span className="feature-group-usage-badge">used by {usageCount}</span>
                  )}
                  <button type="button" className="feature-group-use-btn" onClick={() => onUseInResearch(field.featureId!)}>
                    Use in Research
                  </button>
                </dd>
              )}
            </div>
          );
        })}
      </dl>
    </section>
  );
}
