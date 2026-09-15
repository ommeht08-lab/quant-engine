/**
 * One compact summary-card on the research home page. Purely
 * presentational — every value, loading/unavailable state, and CSS
 * Module class name is resolved by the caller (`ResearchHomeClient`);
 * this component never fabricates a number or decides a color of its
 * own. Mirrors `overview/[ticker]/OverviewClient.tsx`'s own
 * `.metricCard` pattern, where a positive/negative tone is likewise a
 * class name the CALLER chooses, not a prop this component interprets.
 */
export interface HomeMetricCardProps {
  label: string;
  /** A short badge in the card's top-right corner, e.g. "Live", "Local". */
  tag?: string;
  value: string;
  sublabel: string;
  /** The card's own root class (controls its top accent color). */
  cardClassName: string;
  /** Class for the `<i>` tag badge (its own color chip). */
  tagClassName?: string;
  /** Class for the value `<strong>` — e.g. a positive/negative color when the value has one. */
  valueClassName?: string;
}

export default function HomeMetricCard({
  label,
  tag,
  value,
  sublabel,
  cardClassName,
  tagClassName,
  valueClassName,
}: HomeMetricCardProps) {
  return (
    <article className={cardClassName}>
      <div>
        <span>{label}</span>
        {tag && <i className={tagClassName}>{tag}</i>}
      </div>
      <strong className={valueClassName}>{value}</strong>
      <small>{sublabel}</small>
    </article>
  );
}
