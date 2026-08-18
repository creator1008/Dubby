"use client";

import { useEffect, useRef, useState } from "react";
import type { Segment } from "@/lib/ui-types";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import {
  SPEAK_MAX,
  SPEAK_MIN,
  SPEAK_STEP,
  clampSpeakSpeed,
  sourceEndMsOf,
} from "@/lib/speak-rate";

const LANG_NAMES: Record<string, string> = {
  ko: "한국어",
  en: "English",
  vi: "Tiếng Việt",
};

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
  onChange: (id: string, field: "source_text" | "target_text", value: string) => void;
  onSpeakSpeedChange?: (id: string, speed: number) => void;
};

function SpeakRateControl({
  segment,
  disabled,
  onChange,
}: {
  segment: Segment;
  disabled?: boolean;
  onChange?: (speed: number) => void;
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
  const audioRef = useRef<HTMLAudioElement | null>(null);

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
    if (!segment.dubbed_audio_url || disabled) return;
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
    audio.src = segment.dubbed_audio_url;
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
        className={`speak-rate-icon${playing ? " is-active" : ""}`}
        disabled={disabled || !segment.dubbed_audio_url}
        aria-label={playing ? text.voicePreviewStop : text.voicePreview}
        title={playing ? text.voicePreviewStop : text.voicePreview}
        onClick={() => void togglePreview()}
      >
        {playing ? "■" : "▶"}
      </button>
    </div>
  );
}

export function SubtitleEditor({
  segments,
  sourceLang,
  targetLang,
  disabled,
  showSpeakRate = false,
  onChange,
  onSpeakSpeedChange,
}: Props) {
  const text = useAppDictionary();
  const sourceLabel = LANG_NAMES[sourceLang] ?? sourceLang.toUpperCase();
  const targetLabel = LANG_NAMES[targetLang] ?? targetLang.toUpperCase();

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
                {emotionToneLabel(text, seg.emotion_tone) ? (
                  <span>
                    {text.tone}: {emotionToneLabel(text, seg.emotion_tone)}
                  </span>
                ) : null}
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
                  {showSpeakRate ? (
                    <SpeakRateControl
                      segment={seg}
                      disabled={disabled}
                      onChange={(speed) => onSpeakSpeedChange?.(seg.id, speed)}
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
