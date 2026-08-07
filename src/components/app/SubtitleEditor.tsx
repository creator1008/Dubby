"use client";

import type { Segment } from "@/lib/ui-types";
import { useAppDictionary } from "@/lib/i18n/locale-context";

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

/** Map raw speaker ids (A/B/speaker_1) to 1-based display order for 화자 N. */
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
  onChange: (id: string, field: "source_text" | "target_text", value: string) => void;
};

export function SubtitleEditor({
  segments,
  sourceLang,
  targetLang,
  disabled,
  onChange,
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
          return (
            <article className="seg-item" key={seg.id}>
              <div className="seg-meta">
                <span>#{i + 1}</span>
                {speakerNo > 0 && (
                  <span>
                    {text.speaker} {speakerNo}
                  </span>
                )}
                <span>
                  {formatMs(seg.start_ms)} – {formatMs(seg.end_ms)}
                </span>
              </div>
              <div className="seg-pair-grid">
                <label className="seg-field">
                  <span className="sr-only">
                    {text.original} {i + 1}
                  </span>
                  <textarea
                    rows={3}
                    value={seg.source_text}
                    disabled={disabled}
                    placeholder={text.original}
                    onChange={(e) => onChange(seg.id, "source_text", e.target.value)}
                  />
                </label>
                <label className="seg-field">
                  <span className="sr-only">
                    {text.translation} {i + 1}
                  </span>
                  <textarea
                    rows={3}
                    value={seg.target_text}
                    disabled={disabled}
                    placeholder={text.translation}
                    onChange={(e) => onChange(seg.id, "target_text", e.target.value)}
                  />
                </label>
              </div>
              {seg.audio_url && (
                <div className="seg-audio-verify">
                  <span>{text.originalSegment}</span>
                  <audio controls preload="none" src={seg.audio_url} />
                  {seg.dubbed_audio_url && (
                    <>
                      <span>{text.dubbedVoice}</span>
                      <audio controls preload="none" src={seg.dubbed_audio_url} />
                    </>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
