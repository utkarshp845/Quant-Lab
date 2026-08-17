/** Reused wherever a nav area or sub-feature is visibly present in the
 * UI but has no backend to drive it yet (Backtesting/Paper Trading
 * nav items; the Research workspace's Segmentation panel) -- shows
 * the vision honestly instead of hiding it or faking it. */
export function ComingSoonPanel({ title, reason }: { title: string; reason: string }) {
  return (
    <section className="section coming-soon-panel">
      <h2 className="section-title">{title}</h2>
      <p className="section-subtitle">{reason}</p>
    </section>
  );
}
