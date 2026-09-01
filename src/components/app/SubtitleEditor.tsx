"use client";

import { useEffect, useRef, useState } from "react";
import type { Segment, ToneStyle } from "@/lib/ui-types";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import { dubLangDisplayName } from "@/lib/languages";
import {
  SPEAK_MAX,
  SPEAK_MIN,
  SPEAK_STEP,
  clampSpeakSpeed,
  sourceEndMsOf,
} from "@/lib/speak-rate";

const TONE_OPTIONS: ToneStyle[] = [
  "sad",
  "angry",
  "whisper",
  "excited",
  "energetic",
  "calm",
  "cheerful",
];

function formatMs(ms: number) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  const millis = ms % 1000;
  return `${m}:${String(r).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

function emotionToneLabel(
  text: Record<string, string>,
  tone: string | undefined,
): string | null {
  if (!tone) return null;
  const labels: Record<string, string> = {
    sad: text.toneSad,
    angry: text.toneAngry,
    whisper: text.toneWhisper,
    excited: text.toneExcited,
    energetic: text.toneEnergetic,
    calm: text.toneCalm,
    cheerful: text.toneCheerful,
  };
  return labels[tone] ?? tone;
}

function speakerOrderIndex(segments: Segment[], speakerId: string): number {
  const order: string[] = [];
  for (const seg of segments) {
    const id = (seg.speaker_id || "").trim();
    if (id && !order.includes(id)) order.push(id);
  }
  const idx = order.indexOf(speakerId);
  return idx >= 0 ? idx + 1 : 0;
}

type Props = {
  segments: Segment[];
  sourceLang: string;
  targetLang: string;
  disabled?: boolean;
  showSpeakRate?: boolean;
  /** Full source media URL used when a per-segment clip is unavailable. */
  sourceMediaUrl?: string | null;
  defaultEmotionTone?: ToneStyle | string;
  onChange: (id: string, field: "source_text" | "target_text", value: string) => void;
  onSpeakSpeedChange?: (id: string, speed: number) => void;
  onEmotionToneChange?: (id: string, tone: ToneStyle) => void;
  /** Regenerate dubbed preview for one segment; returns a playable URL. */
  onEnsureDubPreview?: (segmentId: string) => Promise<string | undefined>;
};

function SourcePreviewControl({
  segment,
  sourceMediaUrl,
  disabled,
}: {
  segment: Segment;
  sourceMediaUrl?: string | null;
  disabled?: boolean;
}) {
  const text = useAppDictionary();
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const stopAtRef = useRef<number | null>(null);
  const pollRef = useRef<number | null>(null);
  const clipUrl = (segment.audio_url || "").trim();
  const mediaUrl = (sourceMediaUrl || "").trim();
  const canPlay = Boolean(clipUrl || mediaUrl);

  const stopPlayback = () => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
    stopAtRef.current = null;
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      try {
        audio.currentTime = 0;
      } catch {
        /* ignore */
      }
    }
    setPlaying(false);
  };

  useEffect(() => {
    return () => {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      audioRef.current?.pause();
    };
  }, []);

  const togglePreview = async () => {
    if (!canPlay || disabled) return;
    if (playing) {
      stopPlayback();
      return;
    }
    if (!audioRef.current) {
      audioRef.current = new Audio();
      audioRef.current.preload = "auto";
      audioRef.current.onended = () => stopPlayback();
    }
    const audio = audioRef.current;
    const startSec = Math.max(0, segment.start_ms / 1000);
    const endSec = Math.max(startSec + 0.05, sourceEndMsOf(segment) / 1000);

    try {
      if (clipUrl) {
        // Dedicated per-segment clip from extract.
        stopAtRef.current = null;
        audio.src = clipUrl;
        await new Promise<void>((resolve, reject) => {
          const onReady = () => {
            cleanup();
            resolve();
          };
          const onError = () => {
            cleanup();
            reject(new Error("source clip failed"));
          };
          const cleanup = () => {
            audio.removeEventListener("loadedmetadata", onReady);
            audio.removeEventListener("error", onError);
          };
          audio.addEventListener("loadedmetadata", onReady, { once: true });
          audio.addEventListener("error", onError, { once: true });
          audio.load();
        });
        audio.currentTime = 0;
      } else {
        // Full source media: range-play only this subtitle span.
        const base = mediaUrl.split("#")[0];
        // Media Fragments (#t=start,end) clip playback in supporting browsers.
        audio.src = `${base}#t=${startSec.toFixed(3)},${endSec.toFixed(3)}`;
        stopAtRef.current = endSec;
        await new Promise<void>((resolve, reject) => {
          const onReady = () => {
            cleanup();
            resolve();
          };
          const onError = () => {
            cleanup();
            reject(new Error("source media failed"));
          };
          const cleanup = () => {
            audio.removeEventListener("loadedmetadata", onReady);
            audio.removeEventListener("error", onError);
          };
          audio.addEventListener("loadedmetadata", onReady, { once: true });
          audio.addEventListener("error", onError, { once: true });
          audio.load();
        });
        // Seek explicitly — fragments are not honored by every browser/CDN.
        if (Math.abs(audio.currentTime - startSec) > 0.15) {
          await new Promise<void>((resolve) => {
            const onSeeked = () => {
              audio.removeEventListener("seeked", onSeeked);
              resolve();
            };
            audio.addEventListener("seeked", onSeeked, { once: true });
            try {
              audio.currentTime = startSec;
            } catch {
              resolve();
            }
            window.setTimeout(resolve, 400);
          });
        }
        // Hard stop if the fragment/seek path keeps playing past the slot.
        if (pollRef.current != null) window.clearInterval(pollRef.current);
        pollRef.current = window.setInterval(() => {
          const stopAt = stopAtRef.current;
          if (stopAt != null && audio.currentTime >= stopAt - 0.02) {
            stopPlayback();
          }
        }, 50);
      }
      await audio.play();
      setPlaying(true);
    } catch {
      stopPlayback();
    }
  };

  return (
    <div className="source-preview-row">
      <button
        type="button"
        className={`speak-rate-icon${playing ? " is-active" : ""}`}
        disabled={disabled || !canPlay}
        aria-label={playing ? text.voicePreviewStop : text.voicePreview}
        title={
          canPlay
            ? playing
              ? text.voicePreviewStop
              : text.voicePreview
            : text.voicePreviewMissing
        }
        onClick={() => void togglePreview()}
      >
        {playing ? "■" : "▶"}
      </button>
      <span className="source-preview-label">{text.voicePreview}</span>
    </div>
  );
}

function SpeakRateControl({
  segment,
  disabled,
  onChange,
  onEnsureDubPreview,
}: {
  segment: Segment;
  disabled?: boolean;
  onChange?: (speed: number) => void;
  onEnsureDubPreview?: (segmentId: string) => Promise<string | undefined>;
}) {
  const text = useAppDictionary();
  // Natural translation delivery is always 1.0×.
  const baseline = 1;
  const speed = clampSpeakSpeed(segment.speak_speed ?? baseline);
  const clipSpeed = Math.max(
    0.01,
    segment.clip_speak_speed ?? segment.speak_speed ?? baseline,
  );
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const canPreview =
    Boolean(segment.dubbed_audio_url) || Boolean(onEnsureDubPreview);

  useEffect(() => {
    return () => {
      audioRef.current?.pause();
    };
  }, []);

  useEffect(() => {
    if (audioRef.current && playing) {
      audioRef.current.playbackRate = clampSpeakSpeed(speed) / clipSpeed;
    }
  }, [speed, clipSpeed, playing]);

  const setSpeed = (next: number) => {
    onChange?.(clampSpeakSpeed(next));
  };

  const togglePreview = async () => {
    if (disabled || loading) return;
    if (!audioRef.current) {
      audioRef.current = new Audio();
      audioRef.current.onended = () => setPlaying(false);
    }
    const audio = audioRef.current;
    if (playing) {
      audio.pause();
      audio.currentTime = 0;
      setPlaying(false);
      return;
    }
    let url = segment.dubbed_audio_url;
    if (!url && onEnsureDubPreview) {
      setLoading(true);
      try {
        url = await onEnsureDubPreview(segment.id);
      } catch {
        setLoading(false);
        return;
      }
      setLoading(false);
    }
    if (!url) return;
    audio.src = url;
    audio.playbackRate = speed / clipSpeed;
    try {
      await audio.play();
      setPlaying(true);
    } catch {
      setPlaying(false);
    }
  };

  return (
    <div className="speak-rate-row" aria-label={text.speakRate}>
      <button
        type="button"
        className="speak-rate-btn"
        disabled={disabled || speed <= SPEAK_MIN}
        aria-label={text.speakRateSlower}
        onClick={() => setSpeed(speed - SPEAK_STEP)}
      >
        −
      </button>
      <div className="speak-rate-track">
        <input
          type="range"
          min={SPEAK_MIN}
          max={SPEAK_MAX}
          step={SPEAK_STEP}
          value={speed}
          disabled={disabled}
          onChange={(e) => setSpeed(Number(e.target.value))}
        />
        <span className="speak-rate-meta">
          {text.speakRate}: {speed.toFixed(2)}×
        </span>
      </div>
      <button
        type="button"
        className="speak-rate-btn"
        disabled={disabled || speed >= SPEAK_MAX}
        aria-label={text.speakRateFaster}
        onClick={() => setSpeed(speed + SPEAK_STEP)}
      >
        +
      </button>
      <button
        type="button"
        className="speak-rate-icon"
        disabled={disabled || Math.abs(speed - baseline) < 0.001}
        aria-label={text.speakRateReset}
        title={text.speakRateReset}
        onClick={() => setSpeed(baseline)}
      >
        ↺
      </button>
      <button
        type="button"
        className={`speak-rate-icon${playing || loading ? " is-active" : ""}`}
        disabled={disabled || !canPreview || loading}
        aria-label={
          loading
            ? text.voicePreview
            : playing
              ? text.voicePreviewStop
              : text.voicePreview
        }
        title={
          loading
            ? text.voicePreview
            : playing
              ? text.voicePreviewStop
              : text.voicePreview
        }
        onClick={() => void togglePreview()}
      >
        {loading ? "…" : playing ? "■" : "▶"}
      </button>
    </div>
  );
}

function EmotionToneControl({
  segment,
  disabled,
  defaultTone,
  onChange,
}: {
  segment: Segment;
  disabled?: boolean;
  defaultTone?: ToneStyle | string;
  onChange?: (tone: ToneStyle) => void;
}) {
  const text = useAppDictionary();
  const fallback =
    defaultTone && TONE_OPTIONS.includes(defaultTone as ToneStyle)
      ? (defaultTone as ToneStyle)
      : "calm";
  const value =
    segment.emotion_tone && TONE_OPTIONS.includes(segment.emotion_tone as ToneStyle)
      ? (segment.emotion_tone as ToneStyle)
      : fallback;

  return (
    <label className="emotion-tone-row">
      <span className="emotion-tone-label">{text.tone}</span>
      <select
        value={value}
        disabled={disabled}
        aria-label={text.tone}
        onChange={(e) => onChange?.(e.target.value as ToneStyle)}
      >
        {TONE_OPTIONS.map((tone) => (
          <option key={tone} value={tone}>
            {emotionToneLabel(text, tone) ?? tone}
          </option>
        ))}
      </select>
    </label>
  );
}

export function SubtitleEditor({
  segments,
  sourceLang,
  targetLang,
  disabled,
  showSpeakRate = false,
  sourceMediaUrl,
  defaultEmotionTone,
  onChange,
  onSpeakSpeedChange,
  onEmotionToneChange,
  onEnsureDubPreview,
}: Props) {
  const text = useAppDictionary();
  const sourceLabel = dubLangDisplayName(sourceLang, text);
  const targetLabel = dubLangDisplayName(targetLang, text);

  return (
    <div className="subtitle-editor">
      <div className="seg-pair-header" aria-hidden>
        <span>
          {text.original} ({sourceLabel})
        </span>
        <span>
          {text.translation} ({targetLabel})
        </span>
      </div>
      <div className="seg-list">
        {segments.map((seg, i) => {
          const speakerNo = seg.speaker_id
            ? speakerOrderIndex(segments, seg.speaker_id)
            : 0;
          const sourceEnd = sourceEndMsOf(seg);
          return (
            <article className="seg-item" key={seg.id}>
              <div className="seg-meta">
                <span>#{i + 1}</span>
                {speakerNo > 0 && (
                  <span>
                    {text.speaker} {speakerNo}
                  </span>
                )}
              </div>
              <div className="seg-pair-grid">
                <div className="seg-field">
                  <div className="seg-timing" aria-label={`${text.original} timing`}>
                    {formatMs(seg.start_ms)} – {formatMs(sourceEnd)}
                  </div>
                  <label>
                    <span className="sr-only">
                      {text.original} {i + 1}
                    </span>
                    <textarea
                      rows={3}
                      value={seg.source_text}
                      disabled={disabled}
                      placeholder={text.original}
                      onChange={(e) =>
                        onChange(seg.id, "source_text", e.target.value)
                      }
                    />
                  </label>
                  <SourcePreviewControl
                    segment={seg}
                    sourceMediaUrl={sourceMediaUrl}
                    disabled={disabled}
                  />
                </div>
                <div className="seg-field">
                  <div className="seg-timing" aria-label={`${text.translation} timing`}>
                    {formatMs(seg.start_ms)} – {formatMs(seg.end_ms)}
                  </div>
                  <label>
                    <span className="sr-only">
                      {text.translation} {i + 1}
                    </span>
                    <textarea
                      rows={3}
                      value={seg.target_text}
                      disabled={disabled}
                      placeholder={text.translation}
                      onChange={(e) =>
                        onChange(seg.id, "target_text", e.target.value)
                      }
                    />
                  </label>
                  <EmotionToneControl
                    segment={seg}
                    disabled={disabled}
                    defaultTone={defaultEmotionTone}
                    onChange={(tone) => onEmotionToneChange?.(seg.id, tone)}
                  />
                  {showSpeakRate ? (
                    <SpeakRateControl
                      segment={seg}
                      disabled={disabled}
                      onChange={(speed) => onSpeakSpeedChange?.(seg.id, speed)}
                      onEnsureDubPreview={onEnsureDubPreview}
                    />
                  ) : null}
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
