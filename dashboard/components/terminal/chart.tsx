"use client";

import { useEffect, useRef } from "react";
import {
  type Time,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";
import type { MarketBar } from "@/lib/market";

/**
 * The terminal's chart surface (master plan §12 §7.5): TradingView lightweight-charts
 * rendering REAL exported bars only. No synthetic candles — when the export is missing
 * the caller shows its own unavailable state instead of mounting this component with
 * fabricated data.
 */

export interface ChartMarker {
  time: number;
  side: "buy" | "sell";
  text: string;
  /** Position markers read differently from hypothetical signal markers. */
  kind: "signal" | "position";
}

export interface ChartPriceLine {
  price: number;
  title: string;
  color: string;
}

// The market context the strategies themselves read (research/setups.py §Context):
// EMA 50/200 trend and SMA 20/50 crossover, RSI-14 for mean reversion. The MT5 chart
// shows these through the installed indicator's exported lines; the web terminal
// draws the same four so a card's arrow can be checked against the same context here.
const MA_OVERLAYS: { key: "ema_fast" | "ema_slow" | "sma_fast" | "sma_slow"; kind: "ema" | "sma"; period: number; color: string; width: 1 | 2 }[] = [
  { key: "ema_fast", kind: "ema", period: 50, color: "#FBBF24", width: 1 },
  { key: "ema_slow", kind: "ema", period: 200, color: "#A78BFA", width: 2 },
  { key: "sma_fast", kind: "sma", period: 20, color: "#2DD4BF", width: 1 },
  { key: "sma_slow", kind: "sma", period: 50, color: "#34D399", width: 1 },
];

/**
 * Wilder's RSI over the exported window, with the research code's exact indexing
 * (`research/strategies.py::_wilder_rsi`): the seed is the simple mean of the first
 * `period` changes (so the first value lands at bar index `period`), then Wilder's
 * smoothing; a flat run reports 50. Same closes as the candles, so the pane can
 * never disagree with them.
 */
function computeRsiLine(bars: MarketBar[], period = 14): { time: UTCTimestamp; value: number }[] {
  if (bars.length <= period) return [];
  const deltas: number[] = [];
  let flat = true;
  for (let index = 1; index < bars.length; index += 1) {
    const change = bars[index].close - bars[index - 1].close;
    deltas.push(change);
    if (change !== 0) flat = false;
  }
  const value = (gain: number, loss: number): number => {
    if (loss <= 0) return gain > 0 ? 100 : 50;
    return 100 - 100 / (1 + gain / loss);
  };
  const out: { time: UTCTimestamp; value: number }[] = [];
  let avgGain = deltas.slice(0, period).reduce((sum, change) => sum + Math.max(0, change), 0) / period;
  let avgLoss = deltas.slice(0, period).reduce((sum, change) => sum + Math.max(0, -change), 0) / period;
  out.push({ time: bars[period].time as UTCTimestamp, value: value(avgGain, avgLoss) });
  for (let index = period + 1; index < bars.length; index += 1) {
    const change = deltas[index - 1] ?? 0;
    avgGain = (avgGain * (period - 1) + Math.max(0, change)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(0, -change)) / period;
    out.push({ time: bars[index].time as UTCTimestamp, value: value(avgGain, avgLoss) });
  }
  return flat ? [] : out;
}

/**
 * SMA/EMA over the exported window with the research code's exact seeding
 * (`research/strategies.py::_sma/_ema`): causal window, EMA seeded with the SMA of
 * the first `period` bars, earlier bars absent. Computed from the same exported
 * closes the candles show, so the overlay can never disagree with the chart.
 */
function computeMaLine(
  bars: MarketBar[],
  kind: "ema" | "sma",
  period: number,
): { time: UTCTimestamp; value: number }[] {
  if (bars.length < period) return [];
  const out: { time: number; value: number }[] = [];
  if (kind === "sma") {
    let sum = 0;
    for (let index = 0; index < bars.length; index += 1) {
      sum += bars[index].close;
      if (index >= period) sum -= bars[index - period].close;
      if (index >= period - 1) out.push({ time: bars[index].time, value: sum / period });
    }
  } else {
    const alpha = 2 / (period + 1);
    let sum = 0;
    let ema = 0;
    for (let index = 0; index < bars.length; index += 1) {
      const close = bars[index].close;
      if (index < period) {
        sum += close;
        if (index === period - 1) {
          ema = sum / period;
          out.push({ time: bars[index].time, value: ema });
        }
      } else {
        ema = alpha * close + (1 - alpha) * ema;
        out.push({ time: bars[index].time, value: ema });
      }
    }
  }
  return out.map((row) => ({ time: row.time as UTCTimestamp, value: row.value }));
}

export function TerminalChart({
  bars,
  digits,
  style,
  markers,
  priceLines,
}: {
  bars: MarketBar[];
  digits: number | null;
  style: "candles" | "line";
  markers: ChartMarker[];
  priceLines: ChartPriceLine[];
}) {
  // The instrument's own digits drive the price axis precision: JPY quotes show three
  // decimals, EURUSD five — the same numbers the broker shows.
  const precision = digits ?? 2;
  const minMove = 10 ** -precision;

  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick" | "Line"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const rsiRef = useRef<ISeriesApi<"Line"> | null>(null);
  const rsiLinesRef = useRef<ReturnType<ISeriesApi<"Line">["createPriceLine"]>[]>([]);
  const overlaysRef = useRef<ISeriesApi<"Line">[]>([]);
  const linesRef = useRef<ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>[]>([]);
  /** One markers layer per series: setMarkers() replaces its list; a second createSeriesMarkers() would stack a new layer every refresh. */
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  /** The window the series currently holds, so an extension can be appended instead of reset. */
  const windowRef = useRef<{ first: number; last: number; count: number } | null>(null);
  /** When the series is (re)created, the next data payload must be a full set. */
  const resetDataRef = useRef(true);

  // Chart construction depends on the style (candles vs line): one chart per style.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const chart = createChart(host, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#94A3A3",
        fontSize: 11,
        fontFamily: "var(--font-mono)",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.05)" },
        horzLines: { color: "rgba(255,255,255,0.05)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(45,212,191,0.4)", labelBackgroundColor: "#101C1C" },
        horzLine: { color: "rgba(45,212,191,0.4)", labelBackgroundColor: "#101C1C" },
      },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.07)" },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "rgba(255,255,255,0.07)" },
    });
    chartRef.current = chart;
    // Dev-only handle: automated checks need the chart's API (visible range, zoom) to
    // verify refresh behaviour. Never present in a production build.
    if (process.env.NODE_ENV !== "production") {
      (host as unknown as { __chartApi?: IChartApi }).__chartApi = chart;
    }
    const priceFormat = { type: "price", precision, minMove } as const;
    seriesRef.current =
      style === "candles"
        ? chart.addSeries(CandlestickSeries, {
            upColor: "#10B981",
            downColor: "#FB7185",
            borderUpColor: "#10B981",
            borderDownColor: "#FB7185",
            wickUpColor: "#34D399",
            wickDownColor: "#FB7185",
            priceFormat,
          })
        : chart.addSeries(LineSeries, {
            color: "#2DD4BF",
            lineWidth: 2,
            crosshairMarkerVisible: true,
            priceFormat,
          });
    volumeRef.current = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    // The volume pane is ~18% of the chart height, docked to the bottom of the
    // main scale — the standard terminal layout, not a second resizable chart.
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });
    overlaysRef.current = MA_OVERLAYS.map((overlay) =>
      chart.addSeries(LineSeries, {
        color: overlay.color,
        lineWidth: overlay.width,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        priceFormat,
      }),
    );
    // The RSI oscillator's rule — buy below 30, sell above 70 — as its own small
    // pane under the volume band, so a mean-reversion arrow can be checked the same
    // way the MA arrows can. The chart makes the pane when a series lands in it.
    rsiRef.current = chart.addSeries(
      LineSeries,
      {
        color: "#2DD4BF",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      },
      1,
    );
    rsiLinesRef.current = [30, 70].map((level) =>
      rsiRef.current!.createPriceLine({
        price: level,
        color: "rgba(45,212,191,0.45)",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: String(level),
      }),
    );
    for (const pane of chart.panes()) {
      if ((pane as { paneIndex?: () => number }).paneIndex?.() === 1) {
        // A short band under the main pane: enough to read the level, never enough
        // to crowd the candles.
        pane.setStretchFactor(0.28);
        chart.priceScale("right", 1).applyOptions({ borderColor: "rgba(255,255,255,0.07)" });
      }
    }
    resetDataRef.current = true;
    if (process.env.NODE_ENV !== "production") {
      (host as unknown as { __series?: ISeriesApi<"Candlestick" | "Line"> }).__series = seriesRef.current;
    }
    return () => {
      linesRef.current = [];
      markersRef.current = null;
      seriesRef.current = null;
      volumeRef.current = null;
      overlaysRef.current = [];
      rsiRef.current = null;
      rsiLinesRef.current = [];
      chart.remove();
      chartRef.current = null;
      resetDataRef.current = true;
    };
  }, [style, precision, minMove]);

  // Real bars in, chart data out. Times are MT5's own bar epochs.
  //
  // The chart keeps the trader's zoom across refreshes: this effect must never
  // restate what is already on the series. A window that merely extends (the
  // refresh's usual form — one newer bar, and a possibly-updated last bar) is
  // appended with `update()`, which leaves the visible range alone and keeps the
  // right edge pinned when the trader is watching realtime. `setData()` +
  // fitContent() only runs for a genuinely new window: after a series rebuild, a
  // market or frame switch, or an export that rewrote history.
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    if (bars.length === 0) return;
    // The row's shape must match the series: a candlestick series takes the OHLC
    // quartet, a line series plots the close alone as `{time, value}`. Handing an
    // OHLC row to the line style trips lightweight-charts' data assertion
    // ("data value must be a number") the moment the chart mounts or refreshes.
    const toBar = (bar: MarketBar) =>
      style === "line"
        ? { time: bar.time as UTCTimestamp, value: bar.close }
        : {
            time: bar.time as UTCTimestamp,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
          };
    const first = bars[0];
    const last = bars[bars.length - 1];
    const prev = windowRef.current;
    // The volume histogram mirrors the price series exactly — same window, same
    // reset-vs-append discipline — so the two panes can never disagree. Bars the
    // export carries without volume show nothing rather than a fabricated tick count.
    const upBar = (bar: MarketBar, index: number): boolean =>
      index === 0 ? bar.close >= bar.open : bar.close >= (bars[index - 1]?.close ?? bar.open);
    const toVolume = (
      bar: MarketBar,
      up: boolean,
    ): { time: UTCTimestamp; value: number; color: string } | null => {
      if (bar.volume === null || bar.volume <= 0) return null;
      return {
        time: bar.time as UTCTimestamp,
        value: bar.volume,
        // Colour follows the bar's direction against the previous close, the
        // convention the terminal uses on its own volume histogram.
        color: up ? "rgba(16,185,129,0.45)" : "rgba(251,113,133,0.45)",
      };
    };
    const extendsWindow =
      !resetDataRef.current &&
      prev !== null &&
      prev.first === first.time &&
      prev.last <= last.time &&
      prev.count <= bars.length;
    // The context overlays share the price pane's window discipline: a full set on
    // a rebuild, tail updates on extension, never restating history.
    const rsi = computeRsiLine(bars);
    const feedRsi = (reset: boolean) => {
      if (!rsiRef.current) return;
      if (reset) {
        rsiRef.current.setData(rsi as never);
      } else {
        const cutoff = rsi.findIndex((row) => row.time >= (prev?.last ?? 0));
        for (const row of rsi.slice(Math.max(0, cutoff))) rsiRef.current.update(row as never);
      }
    };
    const feedOverlays = (reset: boolean, startIndex: number) => {
      for (let position = 0; position < MA_OVERLAYS.length; position += 1) {
        const overlay = MA_OVERLAYS[position];
        const line = overlaysRef.current[position];
        if (!line) continue;
        if (reset) {
          line.setData(computeMaLine(bars, overlay.kind, overlay.period) as never);
        } else {
          const values = computeMaLine(bars, overlay.kind, overlay.period);
          const cutoff = startIndex === 0 ? 0 : values.findIndex((row) => row.time >= (prev?.last ?? 0));
          for (const row of values.slice(Math.max(0, cutoff))) line.update(row as never);
        }
      }
    };
    if (!extendsWindow) {
      const data = bars.map(toBar);
      series.setData(data as never);
      volumeRef.current?.setData(
        bars.flatMap((bar, index) => {
          const row = toVolume(bar, upBar(bar, index));
          return row ? [row] : [];
        }) as never,
      );
      feedOverlays(true, 0);
      feedRsi(true);
      chart.timeScale().fitContent();
      resetDataRef.current = false;
    } else if (prev.count !== bars.length || prev.last !== last.time) {
      // From the previous last bar onward — its close may have changed, and every
      // bar after it is new. Chronological update() calls, no zoom disturbance.
      const from = bars.findIndex((bar) => bar.time >= prev.last);
      for (let index = Math.max(0, from); index < bars.length; index += 1) {
        const bar = bars[index];
        series.update(toBar(bar) as never);
        const row = toVolume(bar, upBar(bar, index));
        if (row) volumeRef.current?.update(row as never);
      }
      feedOverlays(false, from);
      feedRsi(false);
    }
    windowRef.current = { first: first.time, last: last.time, count: bars.length };
  }, [bars, style]);

  // Signal and position markers: hypothetical vs real, both from actual artifacts.
  // The markers layer is created once per series and refreshed with setMarkers(),
  // so each refresh REPLACES its list instead of stacking another layer. Marker
  // times only render on exact bar times — a timestamp between bar opens is
  // silently dropped by the library — so each marker is snapped to the nearest
  // exported bar open (and dropped when it names a market that has no bar here).
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    if (!markersRef.current) markersRef.current = createSeriesMarkers(series, []);
    const barTimes = bars.map((bar) => bar.time);
    const interval = barTimes.length >= 2 ? barTimes[1] - barTimes[0] : 0;
    const snap = (time: number): number | null => {
      const exact = barTimes.indexOf(time);
      if (exact !== -1) return time;
      // A time that falls INSIDE a bar's coverage belongs to that bar: a trade taken
      // 20 minutes into an hour is drawn on that hour's candle, not on whichever bar
      // OPEN happens to be nearest (past :30 that flips to the next bar). Binary
      // search over the sorted opens for the containing bar.
      let lo = 0;
      let hi = barTimes.length - 1;
      if (interval > 0 && time >= barTimes[0]) {
        let low = 0;
        let high = barTimes.length - 1;
        while (low <= high) {
          const mid = (low + high) >> 1;
          if (barTimes[mid] <= time) {
            if (time < barTimes[mid] + interval) return barTimes[mid];
            low = mid + 1;
          } else {
            high = mid - 1;
          }
        }
        // Time is beyond the last bar's coverage — no bar holds it.
        if (time >= barTimes[barTimes.length - 1] + interval) return null;
      }
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (barTimes[mid] < time) lo = mid;
        else hi = mid;
      }
      const candidates = barTimes.length > 1 ? [barTimes[lo], barTimes[hi]] : [barTimes[0]];
      let best = candidates[0];
      for (const candidate of candidates) {
        if (Math.abs(candidate - time) < Math.abs(best - time)) best = candidate;
      }
      // Identity scale: candlestick series use index coordinates, so a marker's
      // time must equal a bar time exactly — the snap closes that gap, and markers
      // more than half a bar away from any open stay off the chart.
      if (interval > 0 && Math.abs(best - time) > interval / 2) return null;
      return best;
    };
    const mapped: SeriesMarker<UTCTimestamp>[] = [];
    for (const marker of markers) {
      const time = snap(marker.time);
      if (time === null) continue;
      mapped.push({
        time: time as UTCTimestamp,
        position: marker.side === "buy" ? "belowBar" : "aboveBar",
        color: marker.kind === "position" ? "#FBBF24" : marker.side === "buy" ? "#2DD4BF" : "#FB7185",
        shape: marker.side === "buy" ? "arrowUp" : "arrowDown",
        text: marker.text,
      });
    }
    // The layer searches markers by binary search, so its list must be ordered by
    // time ascending — an out-of-order list makes markers vanish or draw incomplete
    // at some zoom levels even though every entry itself is valid.
    mapped.sort((a, b) => (a.time as number) - (b.time as number));
    try {
      markersRef.current.setMarkers(mapped);
      if (process.env.NODE_ENV !== "production") {
        (hostRef.current as unknown as { __markers?: unknown }).__markers = mapped;
      }
    } catch {
      /* markers are decoration; never let them break the chart */
    }
  }, [markers, bars]);

  // Reference lines (last price, entry, SL, TP) from real values only.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    for (const line of linesRef.current) {
      try {
        series.removePriceLine(line);
      } catch {
        /* series was recreated */
      }
    }
    linesRef.current = [];
    for (const spec of priceLines) {
      try {
        linesRef.current.push(
          series.createPriceLine({
            price: spec.price,
            color: spec.color,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: spec.title,
          }),
        );
      } catch {
        /* out-of-range price: skip the line, keep the chart */
      }
    }
  }, [priceLines, style]);

  return <div ref={hostRef} className="chart-canvas" role="img" aria-label="Price chart of real exported bars" />;
}
