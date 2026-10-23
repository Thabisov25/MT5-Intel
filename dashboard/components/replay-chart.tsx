"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesPrimitive,
  type SeriesAttachedParameter,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
// Types only from the client-safe module: `@/lib/replay` reads disk and cannot be bundled
// into a client component's chunk.
import type { FamilyReplay, ReplayTrade } from "@/lib/replay-constants";

/**
 * The strategy page's replay surface: the family's real exported bars with every graded
 * trade drawn as a translucent box from entry to the barrier that resolved it —
 * green (entry → target) when it hit TP, red (entry → stop) when it hit the stop, amber
 * (entry → exit price) on a time exit or a trade still running.
 *
 * Every box is one row of the replay artifact, which is graded by the research core's own
 * pinned trio (`one_at_a_time` / `walk_trade` / `closed_r`) — the same trades the Live tab
 * and the research tables report. Nothing here re-derives a result; it draws what the
 * engine already decided.
 *
 * Boxes are drawn by a lightweight-charts primitive on the chart's own canvas (behind the
 * candles, `zOrder: 'bottom'`), so they pan and zoom with the chart for free and can never
 * drift out of alignment with it the way an HTML overlay would.
 */

/** The bitmap-space target a primitive renderer receives (fancy-canvas shape, typed structurally). */
interface BitmapTarget {
  useBitmapCoordinateSpace(
    fn: (scope: {
      context: CanvasRenderingContext2D;
      horizontalPixelRatio: number;
      verticalPixelRatio: number;
    }) => void,
  ): void;
}

interface ReplayBox {
  from: number;
  to: number;
  /** The bar before `to`, so the box can be extended through the candle it ended on. */
  beforeTo: number | null;
  top: number;
  bottom: number;
  fill: string;
}

const GREEN = "rgba(16, 185, 129, 0.22)";
const RED = "rgba(251, 113, 133, 0.22)";
const AMBER = "rgba(251, 191, 36, 0.20)";

/** One box per trade: entry → the price that decided it, coloured by how it ended. */
export function boxesFor(trades: ReplayTrade[], barTimes: number[]): ReplayBox[] {
  const boxes: ReplayBox[] = [];
  for (const trade of trades) {
    const fill = trade.hit === "tp" ? GREEN : trade.hit === "sl" ? RED : AMBER;
    // A resolved trade spans entry to the barrier it met; a time exit (or a trade still
    // running) spans entry to wherever price actually got to.
    const far = trade.hit === "tp" ? trade.tp : trade.hit === "sl" ? trade.sl : trade.exit;
    const exitIndex = barTimes.indexOf(trade.exit_time);
    boxes.push({
      from: trade.entry_time,
      to: trade.exit_time,
      beforeTo: exitIndex > 0 ? barTimes[exitIndex - 1] : null,
      top: trade.entry,
      bottom: far,
      fill,
    });
  }
  return boxes;
}

class BoxesRenderer implements IPrimitivePaneRenderer {
  private readonly _source: BoxesPrimitive;

  constructor(source: BoxesPrimitive) {
    this._source = source;
  }

  draw(target: BitmapTarget): void {
    const chart = this._source.chart;
    const series = this._source.series;
    if (!chart || !series || this._source.boxes.length === 0) return;
    const timeScale = chart.timeScale();
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const h = scope.horizontalPixelRatio;
      const v = scope.verticalPixelRatio;
      for (const box of this._source.boxes) {
        const x1 = timeScale.timeToCoordinate(box.from as UTCTimestamp);
        const x2 = timeScale.timeToCoordinate(box.to as UTCTimestamp);
        const y1 = series.priceToCoordinate(box.top);
        const y2 = series.priceToCoordinate(box.bottom);
        if (x1 === null || x2 === null || y1 === null || y2 === null) continue;
        // A box drawn to the exit bar's OPEN stops at that candle's left edge; the gap to
        // the previous bar carries it through the candle the trade actually ended on.
        const prev = box.beforeTo === null ? null : timeScale.timeToCoordinate(box.beforeTo as UTCTimestamp);
        const tail = prev === null ? 1 : Math.max(1, x2 - prev);
        const left = Math.round(Math.min(x1, x2) * h);
        const right = Math.round(Math.max(x1, x2) * h + tail * h);
        const top = Math.round(Math.min(y1, y2) * v);
        const bottom = Math.round(Math.max(y1, y2) * v);
        if (right <= left || bottom <= top) continue;
        ctx.fillStyle = box.fill;
        ctx.fillRect(left, top, right - left, bottom - top);
      }
    });
  }
}

class BoxesPrimitive implements ISeriesPrimitive {
  chart: IChartApi | null = null;
  series: ISeriesApi<"Candlestick"> | null = null;
  boxes: ReplayBox[] = [];

  attached(params: SeriesAttachedParameter): void {
    this.chart = params.chart;
    this.series = params.series as ISeriesApi<"Candlestick">;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
  }

  setBoxes(boxes: ReplayBox[]): void {
    this.boxes = boxes;
  }

  zOrder(): "bottom" {
    // Behind the candles: the boxes are context, the bars are the market.
    return "bottom";
  }

  paneViews(): readonly IPrimitivePaneView[] {
    const renderer = new BoxesRenderer(this);
    return [
      {
        renderer: () => renderer,
      },
    ];
  }
}

export function ReplayChart({
  replay,
  digits,
}: {
  replay: FamilyReplay;
  digits: number | null;
}) {
  // The artifact carries the quote's own digits; the prop only lets a caller override.
  const precision = digits ?? replay.digits ?? 3;
  const minMove = 10 ** -precision;

  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const primitiveRef = useRef<BoxesPrimitive | null>(null);
  const markersRef = useRef<ReturnType<typeof createSeriesMarkers<Time>> | null>(null);
  const windowRef = useRef<string | null>(null);

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
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#10B981",
      downColor: "#FB7185",
      borderUpColor: "#10B981",
      borderDownColor: "#FB7185",
      wickUpColor: "#34D399",
      wickDownColor: "#FB7185",
      priceFormat: { type: "price", precision, minMove },
    });
    seriesRef.current = series;
    const primitive = new BoxesPrimitive();
    series.attachPrimitive(primitive);
    primitiveRef.current = primitive;
    return () => {
      markersRef.current = null;
      primitiveRef.current = null;
      seriesRef.current = null;
      chart.remove();
      chartRef.current = null;
      windowRef.current = null;
    };
  }, [precision, minMove]);

  // Data in, candles + boxes + entry arrows out. A replay is a fixed window — it only ever
  // changes wholesale (a re-run), so there is no incremental-append path here: setData and
  // fitContent once per payload identity, and nothing on later renders.
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    const primitive = primitiveRef.current;
    if (!series || !chart || !primitive) return;
    if (replay.bars.length === 0) return;
    const key = `${replay.family}:${replay.generated_utc}:${replay.bars.length}`;
    if (windowRef.current === key) return;
    windowRef.current = key;

    series.setData(
      replay.bars.map((bar) => ({
        time: bar.time as UTCTimestamp,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    );
    primitive.setBoxes(boxesFor(replay.trades, replay.bars.map((bar) => bar.time)));

    // Entry arrows carry the result with them: one look tells you which entries paid.
    const markers: SeriesMarker<Time>[] = replay.trades.map((trade) => ({
      time: trade.entry_time as UTCTimestamp,
      position: trade.side === "buy" ? "belowBar" : "aboveBar",
      color: trade.hit === "tp" ? "#10B981" : trade.hit === "sl" ? "#FB7185" : "#FBBF24",
      shape: trade.side === "buy" ? "arrowUp" : "arrowDown",
      text:
        trade.net_r === null
          ? "open"
          : `${trade.net_r > 0 ? "+" : ""}${trade.net_r.toFixed(1)}R`,
    }));
    const layer = markersRef.current ?? createSeriesMarkers(series, []);
    markersRef.current = layer;
    layer.setMarkers(markers);
    chart.timeScale().fitContent();
  }, [replay]);

  return <div ref={hostRef} className="chart-canvas" role="img" aria-label="Replay of graded trades on real bars" />;
}
