import type { GateHistory, GateHourBucket } from "@/lib/gate-history";

/**
 * Turn the watcher's time buckets into per-code profiles.
 *
 * Every bucket carries its own denominator, because raw counts cannot answer "does this code
 * cluster at a particular time?": an hour with more cycles shows more of everything. The rate
 * (present / cycles) can, and comparing a bucket's rate with the code's overall rate is the
 * actual question, so that ratio is computed here rather than left to the eye.
 */

/** Below this many cycles a bucket's rate is noise, so it is never quoted as a peak. */
export const MIN_CYCLES_FOR_PEAK = 3;

export interface HourCell {
  hour: number;
  cycles: number;
  present: number;
  /** present / cycles, or null when the hour has no cycles to divide by. */
  rate: number | null;
}

export interface SessionCell {
  session: string;
  cycles: number;
  present: number;
  rate: number | null;
  hoursSeen: number[];
}

export interface CodeProfile {
  code: string;
  /** Cycles in the whole window, and how many of them contained this code. */
  cycles: number;
  present: number;
  rate: number | null;
  /** 24 cells, UTC hour 0-23, whether or not the hour has cycles. */
  hours: HourCell[];
  /** The hour with the highest rate among hours with enough cycles, or null. */
  peak: HourCell | null;
  /** Peak rate as a multiple of the overall rate; 1 means no clustering at all. */
  peakVsOverall: number | null;
  /** Session buckets this code appeared in, strongest rate first. */
  sessions: SessionCell[];
}

function rate(present: number, cycles: number): number | null {
  if (cycles <= 0) return null;
  // Clamped: a malformed artifact must not be able to draw a bar past the end of its track.
  return Math.min(1, Math.max(0, present / cycles));
}

export function codeProfiles(history: GateHistory): CodeProfile[] {
  return history.codes.map((stat) => {
    const present = stat.cyclesPresent;
    const overall = rate(present, history.cycles);

    const hours: HourCell[] = history.byHour.map((bucket) => {
      const inHour = bucket.codes[stat.code] ?? 0;
      return {
        hour: bucket.hour,
        cycles: bucket.cycles,
        present: inHour,
        rate: rate(inHour, bucket.cycles),
      };
    });

    const eligible = hours.filter((cell) => cell.cycles >= MIN_CYCLES_FOR_PEAK && cell.present > 0);
    const peak =
      eligible.length > 0
        ? eligible.reduce((best, cell) =>
            (cell.rate ?? 0) > (best.rate ?? 0) ||
            ((cell.rate ?? 0) === (best.rate ?? 0) && cell.present > best.present)
              ? cell
              : best,
          )
        : null;

    const sessions: SessionCell[] = history.bySession
      .map((bucket) => {
        const inSession = bucket.codes[stat.code] ?? 0;
        return {
          session: bucket.session,
          cycles: bucket.cycles,
          present: inSession,
          rate: rate(inSession, bucket.cycles),
          hoursSeen: bucket.hoursSeen,
        };
      })
      .filter((cell) => cell.present > 0)
      .sort((a, b) => (b.rate ?? 0) - (a.rate ?? 0) || a.session.localeCompare(b.session));

    return {
      code: stat.code,
      cycles: history.cycles,
      present,
      rate: overall,
      hours,
      peak,
      peakVsOverall:
        peak && peak.rate !== null && overall !== null && overall > 0 ? peak.rate / overall : null,
      sessions,
    };
  });
}

/** The hours that carried the most cycles, so the reader can see the denominator behind the shape. */
export function busiestHours(history: GateHistory, limit = 3): GateHourBucket[] {
  return [...history.byHour]
    .filter((bucket) => bucket.cycles > 0)
    .sort((a, b) => b.cycles - a.cycles || a.hour - b.hour)
    .slice(0, limit);
}

export function formatHour(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

/** `[9, 10, 11, 14]` -> `09-11, 14`; short runs stay listed, so a gap is never implied away. */
export function formatHourRuns(hours: number[]): string {
  const sorted = [...new Set(hours)].sort((a, b) => a - b);
  const parts: string[] = [];
  let start = 0;
  while (start < sorted.length) {
    let end = start;
    while (end + 1 < sorted.length && sorted[end + 1] === sorted[end] + 1) end += 1;
    const runLength = end - start + 1;
    if (runLength >= 3) parts.push(`${formatHour(sorted[start])}-${formatHour(sorted[end])}`);
    else parts.push(...sorted.slice(start, end + 1).map(formatHour));
    start = end + 1;
  }
  return parts.join(", ");
}

export function formatPercent(value: number | null): string {
  if (value === null) return "—";
  return `${(value * 100).toFixed(value * 100 >= 10 ? 0 : 1)}%`;
}

export function formatMultiple(value: number | null): string {
  if (value === null) return "—";
  return `${value.toFixed(1)}×`;
}
