"use client";

import { useEffect, useRef, useState } from "react";

const MAX_RECORD_SECONDS = 180;

type Props = {
  file: File | null;
  disabled?: boolean;
  onFile: (file: File | null) => void;
  onRecordingChange?: (recording: boolean) => void;
  startLabel: string;
  stopLabel: string;
  listenLabel: string;
  listenStopLabel: string;
  listenFailedLabel: string;
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
  onRecordingChange,
  startLabel,
  stopLabel,
  listenLabel,
  listenStopLabel,
  listenFailedLabel,
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
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAtRef = useRef(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const supported =
    typeof window !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia);

  useEffect(() => {
    return () => {
      stopTracks();
      if (tickRef.current) clearInterval(tickRef.current);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      audioRef.current?.pause();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- cleanup on unmount only
  }, []);

  useEffect(() => {
    onRecordingChange?.(recording);
  }, [recording, onRecordingChange]);

  useEffect(() => {
    if (!file) {
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
        setPreviewUrl(null);
      }
      setPlaying(false);
      audioRef.current?.pause();
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return url;
    });
    setPlaying(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to file identity
  }, [file]);

  const stopTracks = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  const setRecordingState = (next: boolean) => {
    setRecording(next);
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
    setRecordingState(false);
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
      setRecordingState(false);
      stopTracks();
      return;
    }
    recorder.stop();
  };

  const startRecording = async () => {
    if (disabled || recording || !supported) return;
    setError(null);
    audioRef.current?.pause();
    setPlaying(false);
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
        setRecordingState(false);
        stopTracks();
      };
      mediaRef.current = recorder;
      startedAtRef.current = Date.now();
      recorder.start(250);
      setRecordingState(true);
      tickRef.current = setInterval(() => {
        const seconds = (Date.now() - startedAtRef.current) / 1000;
        setElapsed(seconds);
        if (seconds >= MAX_RECORD_SECONDS) {
          const active = mediaRef.current;
          if (active && active.state !== "inactive") active.stop();
        }
      }, 200);
    } catch {
      setError(permissionDeniedLabel);
      stopTracks();
      setRecordingState(false);
    }
  };

  const toggleListen = async () => {
    if (!previewUrl || recording || disabled) return;
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
    audio.src = previewUrl;
    try {
      await audio.play();
      setPlaying(true);
    } catch {
      setPlaying(false);
      setError(listenFailedLabel);
    }
  };

  if (!supported) {
    return <p className="form-msg err">{unsupportedLabel}</p>;
  }

  return (
    <div className="voice-clone-recorder">
      <p className="voice-clone-recorder-hint">{hint}</p>
      <div className="voice-clone-recorder-row">
        <button
          type="button"
          className="btn-secondary"
          disabled={disabled || recording}
          onClick={() => void startRecording()}
        >
          {startLabel}
        </button>
        <button
          type="button"
          className="btn-primary voice-clone-recorder-stop"
          disabled={!recording}
          onClick={stopRecording}
        >
          {stopLabel}
        </button>
        <button
          type="button"
          className="btn-ghost"
          disabled={disabled || recording || !file || !previewUrl}
          onClick={() => void toggleListen()}
        >
          {playing ? listenStopLabel : listenLabel}
        </button>
        {file && !recording ? (
          <button
            type="button"
            className="btn-ghost"
            disabled={disabled}
            onClick={() => {
              audioRef.current?.pause();
              setPlaying(false);
              onFile(null);
              setElapsed(0);
              setError(null);
            }}
          >
            {clearLabel}
          </button>
        ) : null}
      </div>
      <span className="voice-clone-recorder-status" aria-live="polite">
        {recording
          ? `${recordingLabel} ${formatClock(elapsed)} / ${formatClock(MAX_RECORD_SECONDS)}`
          : file
            ? `${readyLabel} · ${formatClock(elapsed || 0)}`
            : formatClock(0)}
      </span>
      {error ? <p className="form-msg err">{error}</p> : null}
    </div>
  );
}
