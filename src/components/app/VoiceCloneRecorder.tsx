"use client";

import { useEffect, useRef, useState } from "react";

const MAX_RECORD_SECONDS = 180;

type Props = {
  file: File | null;
  disabled?: boolean;
  onFile: (file: File | null) => void;
  startLabel: string;
  stopLabel: string;
  clearLabel: string;
  recordingLabel: string;
  readyLabel: string;
  hint: string;
  unsupportedLabel: string;
  permissionDeniedLabel: string;
};

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  for (const type of [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ]) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return undefined;
}

function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

export function VoiceCloneRecorder({
  file,
  disabled = false,
  onFile,
  startLabel,
  stopLabel,
  clearLabel,
  recordingLabel,
  readyLabel,
  hint,
  unsupportedLabel,
  permissionDeniedLabel,
}: Props) {
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAtRef = useRef(0);
  const supported =
    typeof window !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia);

  useEffect(() => {
    return () => {
      stopTracks();
      if (tickRef.current) clearInterval(tickRef.current);
    };
  }, []);

  const stopTracks = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  const finishRecording = (recorder: MediaRecorder) => {
    if (tickRef.current) {
      clearInterval(tickRef.current);
      tickRef.current = null;
    }
    const mime = recorder.mimeType || "audio/webm";
    const blob = new Blob(chunksRef.current, { type: mime });
    chunksRef.current = [];
    stopTracks();
    mediaRef.current = null;
    setRecording(false);
    if (blob.size <= 0) {
      setError(permissionDeniedLabel);
      onFile(null);
      return;
    }
    const ext = mime.includes("mp4")
      ? "m4a"
      : mime.includes("ogg")
        ? "ogg"
        : "webm";
    const recorded = new File([blob], `voice-clone-recording.${ext}`, {
      type: mime,
      lastModified: Date.now(),
    });
    onFile(recorded);
  };

  const stopRecording = () => {
    const recorder = mediaRef.current;
    if (!recorder || recorder.state === "inactive") {
      setRecording(false);
      stopTracks();
      return;
    }
    recorder.stop();
  };

  const startRecording = async () => {
    if (disabled || !supported) return;
    setError(null);
    onFile(null);
    setElapsed(0);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;
      const mimeType = pickMimeType();
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => finishRecording(recorder);
      recorder.onerror = () => {
        setError(permissionDeniedLabel);
        setRecording(false);
        stopTracks();
      };
      mediaRef.current = recorder;
      startedAtRef.current = Date.now();
      recorder.start(250);
      setRecording(true);
      tickRef.current = setInterval(() => {
        const seconds = (Date.now() - startedAtRef.current) / 1000;
        setElapsed(seconds);
        if (seconds >= MAX_RECORD_SECONDS) {
          stopRecording();
        }
      }, 200);
    } catch {
      setError(permissionDeniedLabel);
      stopTracks();
      setRecording(false);
    }
  };

  if (!supported) {
    return <p className="form-msg err">{unsupportedLabel}</p>;
  }

  return (
    <div className="voice-clone-recorder">
      <p className="voice-clone-recorder-hint">{hint}</p>
      <div className="voice-clone-recorder-row">
        {!recording ? (
          <button
            type="button"
            className="btn-secondary"
            disabled={disabled}
            onClick={() => void startRecording()}
          >
            {startLabel}
          </button>
        ) : (
          <button
            type="button"
            className="btn-primary voice-clone-recorder-stop"
            disabled={disabled}
            onClick={stopRecording}
          >
            {stopLabel}
          </button>
        )}
        {file && !recording ? (
          <button
            type="button"
            className="btn-ghost"
            disabled={disabled}
            onClick={() => {
              onFile(null);
              setElapsed(0);
              setError(null);
            }}
          >
            {clearLabel}
          </button>
        ) : null}
        <span className="voice-clone-recorder-status" aria-live="polite">
          {recording
            ? `${recordingLabel} ${formatClock(elapsed)} / ${formatClock(MAX_RECORD_SECONDS)}`
            : file
              ? `${readyLabel} · ${file.name}`
              : formatClock(0)}
        </span>
      </div>
      {error ? <p className="form-msg err">{error}</p> : null}
    </div>
  );
}
