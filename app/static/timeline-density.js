((root) => {
  const buildDensityAxis = entries => {
    const counts = entries.map(entry => Math.max(1, Number(entry.count) || 1));
    const maximum = counts.reduce((largest, count) => Math.max(largest, count), 1);
    const minimum = counts.reduce((smallest, count) => Math.min(smallest, count), maximum);
    const sorted = [...counts].sort((a, b) => a - b);
    const quantile = fraction => {
      if (!sorted.length) return 0;
      const index = (sorted.length - 1) * fraction;
      const lower = Math.floor(index);
      return sorted[lower] + (sorted[Math.ceil(index)] - sorted[lower]) * (index - lower);
    };
    const firstQuartile = quantile(0.25);
    const thirdQuartile = quantile(0.75);
    // Limit rare spikes before compressing the range and easing both endpoints.
    const spread = thirdQuartile - firstQuartile || Math.max(1, thirdQuartile);
    const lowerCount = Math.max(minimum, firstQuartile - 1.5 * spread);
    const upperCount = Math.min(maximum, thirdQuartile + 1.5 * spread);
    const lowerLog = Math.log1p(lowerCount);
    const upperLog = Math.log1p(upperCount);
    const weights = counts.map(count => Math.sqrt(Math.min(count, upperCount)));
    const total = weights.reduce((sum, weight) => sum + weight, 0);
    let offset = 0;
    const bands = entries.map((entry, index) => {
      const start = offset / total * 1000;
      offset += weights[index];
      const end = offset / total * 1000;
      const normalized = upperLog === lowerLog ? 0.5 :
        Math.max(0, Math.min(1, (Math.log1p(counts[index]) - lowerLog) / (upperLog - lowerLog)));
      const heat = normalized * normalized * (3 - 2 * normalized);
      return { date: entry.date, start, end, position: (start + end) / 2, density: 0.25 + 0.75 * heat, heat };
    });
    const byDate = new Map(bands.map(band => [band.date, band]));
    return {
      bands,
      valueFor: date => byDate.get(date)?.position ?? 0,
      densityFor: date => byDate.get(date)?.density ?? 0,
      heatFor: date => byDate.get(date)?.heat ?? 0.5,
      dateFor(value) {
        if (!bands.length) return null;
        const position = Math.max(0, Math.min(1000, Number(value) || 0));
        let low = 0;
        let high = bands.length - 1;
        while (low < high) {
          const middle = Math.floor((low + high) / 2);
          if (bands[middle].end <= position) low = middle + 1;
          else high = middle;
        }
        return bands[low].date;
      },
    };
  };
  if (typeof module !== "undefined" && module.exports) module.exports = buildDensityAxis;
  else root.buildDensityAxis = buildDensityAxis;
})(globalThis);
