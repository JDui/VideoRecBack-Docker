((root) => {
  const buildDensityAxis = entries => {
    const weights = entries.map(entry => Math.sqrt(Math.max(1, Number(entry.count) || 1)));
    const total = weights.reduce((sum, weight) => sum + weight, 0);
    let offset = 0;
    const bands = entries.map((entry, index) => {
      const start = offset / total * 1000;
      offset += weights[index];
      const end = offset / total * 1000;
      return { date: entry.date, start, end, position: (start + end) / 2 };
    });
    const byDate = new Map(bands.map(band => [band.date, band]));
    return {
      bands,
      valueFor: date => byDate.get(date)?.position ?? 0,
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
