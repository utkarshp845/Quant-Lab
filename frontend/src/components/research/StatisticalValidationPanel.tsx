import { useEffect, useState } from "react";
import { getStatisticalValidation, getStatisticalValidationV2 } from "../../api/client";
import type { StatisticalValidationReport } from "../../types/statisticalValidation";
import type { StatisticalValidationReportV2 } from "../../types/statisticalValidationV2";
import { InfoDisclosure } from "../InfoDisclosure";
import { apiErrorMessage, fmtNumberOrDash } from "../../utils/researchFormat";

function pct(x: number, digits = 2): string {
  return `${(x * 100).toFixed(digits)}%`;
}

/**
 * COMPARE + VALIDATE stages, together (they read the same underlying
 * report): "is the setup different from an unconditional baseline, and
 * how much evidence is there that difference is meaningful rather than
 * noise?" Both V1 (backend/app/statistical_validation/) and V2
 * (backend/app/statistical_validation/v2/, dependence-aware) reports
 * are fetched and shown -- V2 is the methodologically preferred one
 * (episode-level AND a dependence-corrected baseline); V1 is shown
 * alongside for the raw-vs-episode robustness comparison it already
 * makes internally. Neither report is recomputed here -- every number
 * came directly from the backend.
 */
export function StatisticalValidationPanel({ backtestId }: { backtestId: string }) {
  const [v1, setV1] = useState<StatisticalValidationReport | null>(null);
  const [v2, setV2] = useState<StatisticalValidationReportV2 | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([getStatisticalValidation(backtestId), getStatisticalValidationV2(backtestId)])
      .then(([r1, r2]) => {
        setV1(r1);
        setV2(r2);
      })
      .catch((err) => setError(apiErrorMessage(err, "Could not compute statistical validation for this backtest.")))
      .finally(() => setLoading(false));
  }, [backtestId]);

  if (loading) return <p className="research-gap-note">Computing statistical validation…</p>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!v1 || !v2) return null;

  const primaryHorizon = v1.horizons.find((h) => h.is_primary) ?? v1.horizons[0];

  return (
    <div className="statistical-validation-panel">
      {/* ---- COMPARE ---- */}
      <h3 className="experiment-form-subheading">Compare — setup vs. baseline</h3>
      <p className="section-subtitle">
        Baseline: the same symbol's unconditional forward return over the same data and window --
        never a hand-picked comparison set.
      </p>
      <div className="table-wrap">
        <table className="payoff-table">
          <thead>
            <tr>
              <th />
              <th>Setup (episode-level)</th>
              <th>Baseline</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Sample size</td>
              <td>{primaryHorizon.sample_sizes.episode_count} episodes ({primaryHorizon.sample_sizes.raw_signal_count} raw)</td>
              <td>{primaryHorizon.sample_sizes.baseline_count}</td>
            </tr>
            <tr>
              <td>Mean forward return</td>
              <td>{pct(primaryHorizon.mean_difference.conditioned_mean)}</td>
              <td>{pct(primaryHorizon.mean_difference.baseline_mean)}</td>
            </tr>
            <tr>
              <td>Directional (win) rate</td>
              <td>{primaryHorizon.win_rate_difference.conditioned_win_rate.toFixed(1)}%</td>
              <td>{primaryHorizon.win_rate_difference.baseline_win_rate.toFixed(1)}%</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="research-gap-note">
        Difference: {pct(primaryHorizon.mean_difference.difference)} (95% CI [{pct(primaryHorizon.mean_difference.ci_low)},{" "}
        {pct(primaryHorizon.mean_difference.ci_high)}]). MFE/MAE-vs-baseline comparison is not computed by
        either engine yet.
      </p>

      {/* ---- VALIDATE ---- */}
      <h3 className="experiment-form-subheading">Validate — how much evidence is there?</h3>
      <p className="section-subtitle">
        <strong>Research</strong> asked "is there an interesting relationship?" <strong>Statistical
        validation</strong> asks "how much evidence is there that it's meaningful rather than noise?"
        These are different questions -- a result can be interesting without being statistically
        robust, or statistically significant without being economically or tradeably meaningful.
      </p>

      <div className="table-wrap">
        <table className="payoff-table">
          <thead>
            <tr>
              <th>Method</th>
              <th>p-value (two-sided)</th>
              <th>Mean difference</th>
              <th>95% CI</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>V2 — Method A (non-overlapping windows)</td>
              <td>{v2.method_a_test.p_value_two_sided.toFixed(4)}</td>
              <td>{pct(v2.method_a_mean_difference.difference)}</td>
              <td>
                [{pct(v2.method_a_mean_difference.ci_low)}, {pct(v2.method_a_mean_difference.ci_high)}]
              </td>
            </tr>
            <tr>
              <td>V2 — Method B (moving block bootstrap)</td>
              <td>{v2.method_b_test.p_value_two_sided.toFixed(4)}</td>
              <td>{pct(v2.method_b_mean_difference.difference)}</td>
              <td>
                [{pct(v2.method_b_mean_difference.ci_low)}, {pct(v2.method_b_mean_difference.ci_high)}]
              </td>
            </tr>
            <tr>
              <td>V1 — episode-level (baseline treated as independent, less rigorous)</td>
              <td>{v1.primary_permutation_test.p_value_two_sided.toFixed(4)}</td>
              <td>{pct(v1.primary_permutation_test.observed_mean_difference)}</td>
              <td>—</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p className={v2.robustness.conclusion_changes_materially ? "research-warning research-warning-warning" : "research-gap-note"}>
        {v2.robustness.conclusion_changes_materially
          ? "The two dependence-aware methods disagree on whether the confidence interval excludes zero -- treat this result as fragile, not robust."
          : "Both dependence-aware methods agree on whether the confidence interval excludes zero."}
      </p>

      <dl className="feature-explorer-dataset-grid">
        <div>
          <dt>Effect size (Cohen's d, Method A)</dt>
          <dd>
            {v2.effect_size.cohens_d.toFixed(3)} ({v2.effect_size.interpretation})
          </dd>
        </div>
        <div>
          <dt>Minimum detectable effect size</dt>
          <dd>{fmtNumberOrDash(v2.power_analysis.minimum_detectable_effect_size, 3)}</dd>
        </div>
        <div>
          <dt>Observed effect below detectable threshold?</dt>
          <dd>{v2.power_analysis.observed_effect_below_detectable_threshold ? "yes — underpowered" : "no"}</dd>
        </div>
      </dl>

      <InfoDisclosure title="+ Assumptions &amp; limitations">
        <ul className="research-interpretation-list">
          <li>Episode grouping: {v1.episode_rule.description}</li>
          <li>Only the primary horizon ({v1.primary_window_bars} bars) is a confirmatory test — secondary horizons are descriptive only, never significance-tested (no multiple-comparison correction needed because none is attempted).</li>
          <li>Seed {v2.seed}, {v2.n_resamples} resamples, block length multiplier {v2.block_length_multiplier} — reproducible, not re-picked per run.</li>
          <li>Neither method claims causality — both test whether the conditioned population differs from the unconditional baseline by more than resampling variation would explain.</li>
        </ul>
      </InfoDisclosure>

      <InfoDisclosure title="+ Statistical vs. economic vs. trading significance">
        <p>
          <strong>Statistical significance</strong> (this section): is the observed difference larger
          than random resampling variation would typically produce? A low p-value / a CI excluding zero
          answers this and only this.
        </p>
        <p>
          <strong>Economic significance</strong>: is the effect size large enough to matter in the real
          world, independent of statistical confidence? A tiny, statistically "significant" effect can
          be economically irrelevant; a large effect can be statistically uncertain with a small sample.
        </p>
        <p>
          <strong>Trading significance</strong>: would this actually be tradeable after transaction
          costs, slippage, position sizing, and execution constraints? This validation says nothing
          about that — see the Backtest stage's own "Strategy Definition required" limitation.
        </p>
        <p>A statistically significant result is never, by itself, proof of profitability.</p>
      </InfoDisclosure>
    </div>
  );
}
