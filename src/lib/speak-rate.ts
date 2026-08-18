/** Speak-rate ↔ timestamp helpers for the subtitle editor. */

import type { Segment } from "@/lib/ui-types";

export const SPEAK_MIN = 0.5;
export const SPEAK_MAX = 1.5;
export const SPEAK_STEP = 0.05;
const END_PAD_MS = 80;
const MIN_SLOT_MS = 120;

/** Approx spoken characters/sec at speed=1.0 (mirrors api/app/worker/timing.py). */
const CHARS_PER_SECOND: Record<string, number> = {
  en: 14.5,
  es: 14.0,
  fr: 13.5,
  pt: 13.5,
  de: 13.0,
  id: 13.0,
  ms: 13.0,
  tr: 12.5,
  vi: 12.0,
  ru: 11.5,
  ar: 11.0,
  ur: 11.0,
  ko: 8.5,
  ja: 7.5,
  zh: 6.5,
  ta: 10.0,
};

export function clampSpeakSpeed(value: number): number {
  const stepped = Math.round(value / SPEAK_STEP) * SPEAK_STEP;
  return Math.min(SPEAK_MAX, Math.max(SPEAK_MIN, Number(stepped.toFixed(2))));
}

export function estimateTtsSeconds(text: string, language = ""): number {
  const compact = text.replace(/\s+/g, "");
  if (!compact) return 0;
  const lang = language.trim().toLowerCase().split("-")[0] ?? "";
  const cps = CHARS_PER_SECOND[lang] ?? 12;
  const pauses = [".", "!", "?", "。", "？", "！", "…"].reduce(
    (sum, mark) => sum + (compact.split(mark).length - 1),
    0,
  );
  return Math.max(0.35, compact.length / cps + 0.12 * pauses);
}

function sourceRelativePace(
  sourceText: string,
  sourceLang: string,
  slotSeconds: number,
): number {
  if (slotSeconds <= 0) return 1;
  const estimated = estimateTtsSeconds(sourceText, sourceLang);
  if (estimated <= 0) return 1;
  return Math.max(0.55, Math.min(1.85, estimated / slotSeconds));
}

/** Match dub pace to the source speaker (same idea as the worker). */
export function speakSpeedMatchingSource(
  sourceText: string,
  sourceLang: string,
  targetText: string,
  targetLang: string,
  slotSeconds: number,
): number {
  const pace = sourceRelativePace(sourceText, sourceLang, slotSeconds);
  const targetEst = estimateTtsSeconds(targetText, targetLang);
  const paced = pace > 0 ? targetEst / pace : targetEst;
  if (paced > slotSeconds * 1.08 && slotSeconds > 0) {
    return clampSpeakSpeed(Math.max(paced / slotSeconds, SPEAK_MIN));
  }
  return clampSpeakSpeed(pace);
}

export function speakSpeedForSlot(
  clipSeconds: number,
  slotSeconds: number,
): number {
  if (clipSeconds <= 0 || slotSeconds <= 0) return 1;
  if (clipSeconds <= slotSeconds * 1.03) return 1;
  return clampSpeakSpeed(clipSeconds / slotSeconds);
}

export function videoEndMsFromSegments(
  segments: Segment[],
  durationSeconds: number | null | undefined,
): number {
  const fromDuration =
    typeof durationSeconds === "number" && durationSeconds > 0
      ? Math.round(durationSeconds * 1000)
      : 0;
  const fromSegments = segments.reduce(
    (max, row) => Math.max(max, row.end_ms),
    0,
  );
  return Math.max(fromDuration, fromSegments);
}

export function maxEndMsForSegment(
  segments: Segment[],
  segmentId: string,
  videoEndMs: number,
): number {
  const ordered = [...segments].sort((a, b) => a.idx - b.idx);
  const index = ordered.findIndex((row) => row.id === segmentId);
  if (index < 0) return videoEndMs;
  const next = ordered[index + 1];
  if (next) return Math.max(ordered[index].start_ms + MIN_SLOT_MS, next.start_ms - END_PAD_MS);
  return Math.max(ordered[index].start_ms + MIN_SLOT_MS, videoEndMs);
}

function contentDurationMs(segment: Segment): number {
  const speed = segment.speak_speed ?? 1;
  const slot = Math.max(MIN_SLOT_MS, segment.end_ms - segment.start_ms);
  return slot * Math.max(SPEAK_MIN, speed);
}

/**
 * Apply a speak-rate change and grow/shrink ``end_ms`` to match.
 * Cannot push past the next segment start or the video end — speed is
 * clamped to the slowest rate that still fits.
 */
export function applySpeakRateChange(
  segments: Segment[],
  segmentId: string,
  requestedSpeed: number,
  videoEndMs: number,
): Segment[] {
  const maxEnd = maxEndMsForSegment(segments, segmentId, videoEndMs);
  return segments.map((row) => {
    if (row.id !== segmentId) return row;
    const contentMs = contentDurationMs(row);
    const maxSlot = Math.max(MIN_SLOT_MS, maxEnd - row.start_ms);
    const minSpeedForCap = contentMs / maxSlot;
    const speed = clampSpeakSpeed(Math.max(requestedSpeed, minSpeedForCap));
    const desiredEnd = row.start_ms + Math.round(contentMs / Math.max(0.01, speed));
    const endMs = Math.min(maxEnd, Math.max(row.start_ms + MIN_SLOT_MS, desiredEnd));
    return { ...row, speak_speed: speed, end_ms: endMs };
  });
}

/**
 * When translation text changes, recompute speak speed (and extend end when
 * needed) so the line still fits without overlapping the next stamp.
 */
export function refitSegmentAfterTranslation(
  segment: Segment,
  segments: Segment[],
  sourceLang: string,
  targetLang: string,
  videoEndMs: number,
): Segment {
  const maxEnd = maxEndMsForSegment(segments, segment.id, videoEndMs);
  const slotSec = Math.max(0.12, (segment.end_ms - segment.start_ms) / 1000);
  let speed = speakSpeedMatchingSource(
    segment.source_text,
    sourceLang,
    segment.target_text,
    targetLang,
    slotSec,
  );
  const natural = estimateTtsSeconds(segment.target_text, targetLang);
  let needSec = natural / Math.max(0.01, speed);
  let endMs = segment.end_ms;
  if (needSec > slotSec + 0.02) {
    const desired = segment.start_ms + Math.ceil(needSec * 1000);
    endMs = Math.min(maxEnd, Math.max(segment.end_ms, desired));
  }
  const fittedSlot = Math.max(0.12, (endMs - segment.start_ms) / 1000);
  if (natural / Math.max(0.01, speed) > fittedSlot * 1.03) {
    speed = speakSpeedForSlot(natural, fittedSlot);
  }
  return {
    ...segment,
    end_ms: endMs,
    speak_speed: speed,
    baseline_speak_speed: 1,
  };
}

export function prepareSegmentsForSave(
  segments: Segment[],
  baselineTargets: Record<string, string>,
  sourceLang: string,
  targetLang: string,
  videoEndMs: number,
): Segment[] {
  let next = segments;
  for (const row of segments) {
    const baseline = baselineTargets[row.id] ?? row.target_text;
    if (baseline !== row.target_text) {
      const fitted = refitSegmentAfterTranslation(
        row,
        next,
        sourceLang,
        targetLang,
        videoEndMs,
      );
      next = next.map((s) => (s.id === row.id ? fitted : s));
    }
  }
  return next;
}
