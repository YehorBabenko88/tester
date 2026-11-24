const DAY = 86400000;
export function bucketStart(ts, interval) {
  const d = new Date(ts);
  if (interval === "M") return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
  if (interval === "W") {
    const midnight = Date.UTC(
      d.getUTCFullYear(),
      d.getUTCMonth(),
      d.getUTCDate(),
    );
    return midnight - ((d.getUTCDay() + 6) % 7) * DAY;
  }
  const width = interval === "D" ? DAY : Number(interval) * 60000;
  if (!Number.isFinite(width) || width <= 0)
    throw Error("Unsupported timeframe");
  return Math.floor(ts / width) * width;
}
export function nextBoundary(ts, interval) {
  const start = bucketStart(ts, interval);
  if (interval === "M") {
    const d = new Date(start);
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
  }
  return (
    start +
    (interval === "W"
      ? 7 * DAY
      : interval === "D"
        ? DAY
        : Number(interval) * 60000)
  );
}
export function bucketWidth(ts, interval) {
  return nextBoundary(ts, interval) - bucketStart(ts, interval);
}
function moveMonth(ts, step) {
  const d = new Date(ts),
    day = d.getUTCDate();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + step);
  const last = new Date(
    Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0),
  ).getUTCDate();
  d.setUTCDate(Math.min(day, last));
  return d.getTime();
}
export function shiftRange(range, interval, step) {
  const moved = range.map((ts) =>
    interval === "M"
      ? moveMonth(ts, step)
      : ts +
        step *
          (interval === "W"
            ? 7 * DAY
            : interval === "D"
              ? DAY
              : Number(interval) * 60000),
  );
  if (moved[1] <= moved[0] && range[1] > range[0])
    moved[1] = moved[0] + range[1] - range[0];
  return moved;
}
