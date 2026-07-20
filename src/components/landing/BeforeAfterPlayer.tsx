"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Segment, SubtitleMode } from "@/lib/ui-types";
import { useAppDictionary } from "@/lib/i18n/locale-context";

type ListenMode = "before" | "after" | null;

type Props = {
  beforeSrc: string;
  afterSrc?: string;
  beforeLabel?: string;
  afterLabel?: string;
  listenBeforeLabel?: string;
  listenAfterLabel?: string;
  pauseLabel?: string;
  segments?: Segment[];
  subtitleMode?: SubtitleMode | string;
};

export function BeforeAfterPlayer({
  beforeSrc,
  afterSrc,
  beforeLabel,
  afterLabel,
  listenBeforeLabel,
  listenAfterLabel,
  pauseLabel,
  subtitleMode = "none",
}: Props) {
  const text = useAppDictionary();
  const beforeRef = useRef<HTMLVideoElement>(null);
  const afterRef = useRef<HTMLVideoElement>(null);
  const [active, setActive] = useState<ListenMode>(null);
  const [popup, setPopup] = useState<ListenMode>(null);
  const resolvedBeforeLabel = beforeLabel ?? text.original;
  const resolvedAfterLabel = afterLabel ?? text.dubbedVoice;
  const resolvedListenBefore = listenBeforeLabel ?? text.originalListen;
  const resolvedListenAfter = listenAfterLabel ?? text.dubbedListen;
  const resolvedPause = pauseLabel ?? text.pause;

  useEffect(() => {
    const before = beforeRef.current;
    const after = afterRef.current;
    if (before) {
      before.pause();
      before.currentTime = 0;
    }
    if (after) {
      after.pause();
      after.currentTime = 0;
    }
  }, [beforeSrc, afterSrc, subtitleMode]);

  useEffect(() => {
    if (!popup) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPopup(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [popup]);

  const stopAll = () => {
    beforeRef.current?.pause();
    afterRef.current?.pause();
    setActive(null);
  };

  const playOnly = async (mode: "before" | "after") => {
    const before = beforeRef.current;
    const after = afterRef.current;
    if (!before || (mode === "after" && (!after || !afterSrc))) return;
    stopAll();
    setPopup(mode);
  };

  return (
    <div className="compare-stage compare-stage-app">
      <div className="compare-grid compare-grid-spaced">
        <figure className="compare-pane">
          <div className="pane-label">{resolvedBeforeLabel}</div>
          <div className="pane-video-frame">
            <video
              ref={beforeRef}
              className="pane-video"
              src={beforeSrc}
              playsInline
              preload="metadata"
              onEnded={() => {
                if (active === "before") setActive(null);
              }}
            />
          </div>
          <button
            type="button"
            className={`pane-listen-btn${active === "before" ? " active" : ""}`}
            onClick={() => playOnly("before")}
          >
            {active === "before" ? resolvedPause : resolvedListenBefore}
          </button>
        </figure>

        <figure className="compare-pane">
          <div className="pane-label pane-label-after">{resolvedAfterLabel}</div>
          <div className="pane-video-frame">
            {afterSrc ? (
              <video
                ref={afterRef}
                className="pane-video"
                src={afterSrc}
                playsInline
                preload="metadata"
                onEnded={() => {
                  if (active === "after") setActive(null);
                }}
              />
            ) : (
              <div className="pane-video pane-video-empty">
                <span>{text.noDubVideo}</span>
              </div>
            )}
          </div>
          <button
            type="button"
            className={`pane-listen-btn${active === "after" ? " active" : ""}`}
            disabled={!afterSrc}
            onClick={() => playOnly("after")}
          >
            {active === "after" ? resolvedPause : resolvedListenAfter}
          </button>
        </figure>
      </div>
      {popup && createPortal(
        <div
          className="media-popup"
          role="dialog"
          aria-modal="true"
          aria-label={text.fullscreenPlayback}
          onClick={() => setPopup(null)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 1000,
            display: "grid",
            placeItems: "center",
            padding: "clamp(0.75rem, 2vw, 1.5rem)",
            background: "rgb(3 10 14 / 94%)",
            backdropFilter: "blur(10px)",
          }}
        >
          <div
            className="media-popup-content"
            onClick={(event) => event.stopPropagation()}
            style={{
              display: "grid",
              gridTemplateRows: "auto minmax(0, 1fr)",
              width: "min(100%, 1440px)",
              height: "min(100%, 900px)",
            }}
          >
            <div
              className="media-popup-head"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "1rem",
                paddingBottom: "0.75rem",
                color: "#fff",
              }}
            >
              <strong>
                {popup === "before" ? resolvedBeforeLabel : resolvedAfterLabel}
              </strong>
              <button type="button" className="btn-ghost" onClick={() => setPopup(null)}>
                {text.close}
              </button>
            </div>
            <div
              className="media-popup-video-wrap"
              style={{
                position: "relative",
                display: "grid",
                minHeight: 0,
                overflow: "hidden",
                borderRadius: "1rem",
                background: "#000",
              }}
            >
              <video
                className="media-popup-video"
                src={popup === "before" ? beforeSrc : afterSrc}
                controls
                autoPlay
                playsInline
                style={{
                  width: "100%",
                  height: "100%",
                  maxHeight: "calc(100vh - 6rem)",
                  objectFit: "contain",
                }}
              />
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
