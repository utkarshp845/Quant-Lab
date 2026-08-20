import { useEffect, useMemo, useState } from "react";
import { analyzeBearPutSpread, ApiError } from "../api/client";
import { AssumptionsFooter } from "../components/AssumptionsFooter";
import { DeltaSection } from "../components/DeltaSection";
import { InputsPanel, type InputMode } from "../components/InputsPanel";
import { MonteCarloSection } from "../components/MonteCarloSection";
import { PayoffCalculator } from "../components/PayoffCalculator";
import { PayoffChart } from "../components/PayoffChart";
import { PayoffTable } from "../components/PayoffTable";
import { ProbabilityEngineSection } from "../components/ProbabilityEngineSection";
import { ProbabilitySection } from "../components/ProbabilitySection";
import { RiskReward } from "../components/RiskReward";
import { TradeStructure } from "../components/TradeStructure";
import { TradeSummary } from "../components/TradeSummary";
import { VolatilitySection } from "../components/VolatilitySection";
import type { BearPutSpreadRequest, BearPutSpreadResponse } from "../types/bearPutSpread";
import { GRADUATION_EXAMPLE, type BearPutSpreadFormState } from "../types/form";

/** Parses the string-based form state into the numeric API request, or
 * returns null with a reason if any field is not a valid number yet. */
function tryParseRequest(form: BearPutSpreadFormState): BearPutSpreadRequest | null {
  const price = Number(form.underlying.price);
  const dte = Number(form.underlying.dte);
  const longStrike = Number(form.longPut.strike);
  const longBid = Number(form.longPut.bid);
  const longAsk = Number(form.longPut.ask);
  const longDelta = Number(form.longPut.delta);
  const longIv = Number(form.longPut.ivPercent);
  const shortStrike = Number(form.shortPut.strike);
  const shortBid = Number(form.shortPut.bid);
  const shortAsk = Number(form.shortPut.ask);
  const shortDelta = Number(form.shortPut.delta);
  const shortIv = Number(form.shortPut.ivPercent);

  const allValues = [
    price,
    dte,
    longStrike,
    longBid,
    longAsk,
    longDelta,
    longIv,
    shortStrike,
    shortBid,
    shortAsk,
    shortDelta,
    shortIv,
  ];
  if (allValues.some((v) => Number.isNaN(v)) || form.underlying.symbol.trim() === "") {
    return null;
  }

  return {
    underlying: { symbol: form.underlying.symbol.trim(), price, dte },
    long_put: { strike: longStrike, bid: longBid, ask: longAsk, delta: longDelta, iv: longIv / 100 },
    short_put: {
      strike: shortStrike,
      bid: shortBid,
      ask: shortAsk,
      delta: shortDelta,
      iv: shortIv / 100,
    },
  };
}

export function CalculatorPage() {
  const [form, setForm] = useState<BearPutSpreadFormState>(GRADUATION_EXAMPLE);
  const [inputMode, setInputMode] = useState<InputMode>("manual");
  const [data, setData] = useState<BearPutSpreadResponse | null>(null);
  const [fieldErrors, setFieldErrors] = useState<string[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const request = useMemo(() => tryParseRequest(form), [form]);

  useEffect(() => {
    if (!request) {
      setErrorMessage("Enter numeric values for every field to see the analysis.");
      setFieldErrors([]);
      return;
    }

    let cancelled = false;
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const result = await analyzeBearPutSpread(request);
        if (!cancelled) {
          setData(result);
          setErrorMessage(null);
          setFieldErrors([]);
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError) {
            setErrorMessage(err.message);
            setFieldErrors(err.details);
          } else {
            setErrorMessage("Could not reach the backend. Is it running on http://localhost:8000?");
            setFieldErrors([]);
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 300); // debounce: recompute shortly after typing stops

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [request]);

  const longStrike = Number(form.longPut.strike);
  const shortStrike = Number(form.shortPut.strike);
  const underlyingPrice = Number(form.underlying.price);
  const dte = Number(form.underlying.dte);

  return (
    <div className="page">
      <header className="page-header">
        <h1>Calculator</h1>
        <p className="tagline">
          Transparent Bear Put Spread Calculator — a secondary utility, not a research tool. Every
          number traces to a visible formula. Not financial advice. No live data feeds this
          automatically, no brokerage connection, no trade execution. Looking for live quotes,
          streaming, or historical bars? See <strong>Data</strong>.
        </p>
        {loading && <span className="loading-pill">Recalculating…</span>}
      </header>

      <InputsPanel
        form={form}
        onChange={setForm}
        fieldErrors={fieldErrors}
        mode={inputMode}
        onModeChange={setInputMode}
      />

      {errorMessage && (
        <div className="error-banner">
          {errorMessage}
          {data && " The figures below are from the last valid inputs and no longer match what's entered above."}
        </div>
      )}

      {data && (
        <div className={errorMessage ? "results-stale" : undefined}>
          <TradeStructure data={data} longStrike={longStrike} shortStrike={shortStrike} />
          <RiskReward data={data} longStrike={longStrike} shortStrike={shortStrike} />
          <DeltaSection data={data} />
          <VolatilitySection
            data={data}
            underlyingPrice={underlyingPrice}
            dte={dte}
            longIvPercent={Number(form.longPut.ivPercent)}
            shortIvPercent={Number(form.shortPut.ivPercent)}
          />
          <ProbabilitySection data={data} underlyingPrice={underlyingPrice} />
          <PayoffCalculator
            longStrike={longStrike}
            shortStrike={shortStrike}
            debitPerShare={data.debit.debit_per_share}
            defaultPrice={data.risk_reward.breakeven}
          />
          <PayoffTable scenarios={data.payoff_table} />
          <PayoffChart
            chartPoints={data.payoff_chart_points}
            breakeven={data.risk_reward.breakeven}
            longStrike={longStrike}
            shortStrike={shortStrike}
            underlyingPrice={underlyingPrice}
            maxLoss={-data.risk_reward.max_loss_per_contract}
            maxProfit={data.risk_reward.max_profit_per_contract}
          />
          <ProbabilityEngineSection data={data} underlyingPrice={underlyingPrice} />
          <MonteCarloSection
            request={request}
            breakeven={data.risk_reward.breakeven}
            underlyingPrice={underlyingPrice}
            debitPerContract={data.debit.debit_per_contract}
          />
          <TradeSummary data={data} />
        </div>
      )}

      <AssumptionsFooter />
    </div>
  );
}
