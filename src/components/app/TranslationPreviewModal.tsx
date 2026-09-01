"use client";

import type { Segment } from "@/lib/ui-types";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import { dubLangDisplayName } from "@/lib/languages";

function formatMs(ms: number) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

type Props = {
  open: boolean;
  segments: Segment[];
  sourceLang: string;
  targetLang: string;
  onClose: () => void;
};

export function TranslationPreviewModal({
  open,
  segments,
  sourceLang,
  targetLang,
  onClose,
}: Props) {
  const text = useAppDictionary();
  if (!open) return null;

  return (
    <div
      className="auth-modal-backdrop"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="auth-modal translation-preview-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="translation-preview-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="auth-modal-head">
          <h2 id="translation-preview-title">{text.viewTranslationTitle}</h2>
          <button
            type="button"
            className="auth-modal-close"
            onClick={onClose}
            aria-label={text.close}
          >
            ×
          </button>
        </div>
        <p className="muted translation-preview-help">{text.viewTranslationHelp}</p>
        <div className="translation-preview-list">
          {segments.map((segment, index) => (
            <article key={segment.id} className="translation-preview-item">
              <div className="seg-meta">
                <span>#{index + 1}</span>
                <span>
                  {formatMs(segment.start_ms)} – {formatMs(segment.end_ms)}
                </span>
                <span>
                  {dubLangDisplayName(sourceLang, text)} →{" "}
                  {dubLangDisplayName(targetLang, text)}
                </span>
              </div>
              <p className="translation-preview-source">{segment.source_text}</p>
              <p className="translation-preview-target">{segment.target_text}</p>
            </article>
          ))}
        </div>
        <div className="action-row">
          <button type="button" className="btn-secondary" onClick={onClose}>
            {text.close}
          </button>
        </div>
      </div>
    </div>
  );
}
