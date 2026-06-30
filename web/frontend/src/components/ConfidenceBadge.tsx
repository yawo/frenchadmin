interface Props {
  value: number;
  showLabel?: boolean;
}

export default function ConfidenceBadge({ value, showLabel = true }: Props) {
  const pct = (value * 100).toFixed(0);
  let colorClass: string;

  if (value >= 0.85) colorClass = "bg-green-100 text-green-800";
  else if (value >= 0.70) colorClass = "bg-yellow-100 text-yellow-800";
  else colorClass = "bg-orange-100 text-orange-800";

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colorClass}`}
    >
      {showLabel && "Confiance: "}
      {pct}%
    </span>
  );
}
