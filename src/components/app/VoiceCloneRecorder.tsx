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

function writeString(view: DataView, offset: number, value: string) {
  for (let i = 0; i < value.length; i += 1) {
    view.setUint8(offset + i, value.charCodeAt(i));
  }
}

/** Encode decoded PCM to a WAV file so ffprobe/ElevenLabs always see a duration. */
function audioBufferToWav(buffer: AudioBuffer): Blob {
  const channels = Math.min(2, buffer.numberOfChannels);
  const sampleRate = buffer.sampleRate;
  const samples = buffer.length;
  const bytesPerSample = 2;
  const blockAlign = channels * bytesPerSample;
  const dataSize = samples * blockAlign;
  const arrayBuffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(arrayBuffer);

  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bytesPerSample * 8, true);
  writeString(view, 36, "data");
  view.setUint32(40, dataSize, true);

  const channelData = Array.from({ length: channels }, (_, i) =>
    buffer.getChannelData(i),
  );
  let offset = 44;
  for (let i = 0; i < samples; i += 1) {
    for (let ch = 0; ch < channels; ch += 1) {
      const sample = Math.max(-1, Math.min(1, channelData[ch][i] ?? 0));
      view.setInt16(
        offset,
        sample < 0 ? sample * 0x8000 : sample * 0x7fff,
        true,
      );
      offset += 2;
    }
  }
  return new Blob([arrayBuffer], { type: "audio/wav" });
}

async function blobToWavFile(blob: Blob): Promise<File> {
  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext })
      .webkitAudioContext;
  const ctx = new AudioCtx();
  try {
    const raw = await blob.arrayBuffer();
    const audioBuffer = await ctx.decodeAudioData(raw.slice(0));
    const wav = audioBufferToWav(audioBuffer);
    return new File([wav], "voice-clone-recording.wav", {
      type: "audio/wav",
      lastModified: Date.now(),
    });
  } finally {
    await ctx.close().catch(() => undefined);
  }
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
  const [saving, setSaving] = useState(false);
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
    onRecordingChange?.(recording || saving);
  }, [recording, saving, onRecordingChange]);

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

  const finishRecording = async (recorder: MediaRecorder) => {
    if (tickRef.current) {
      clearInterval(tickRef.current);
      tickRef.current = null;
    }
    const finalElapsed = Math.min(
      MAX_RECORD_SECONDS,
      (Date.now() - startedAtRef.current) / 1000,
    );
    setElapsed(finalElapsed);
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
    setSaving(true);
    setError(null);
    try {
      // WAV has reliable duration metadata; raw MediaRecorder webm often probes as 0s.
      const wavFile = await blobToWavFile(blob);
      onFile(wavFile);
    } catch {
      const ext = mime.includes("mp4")
        ? "m4a"
        : mime.includes("ogg")
          ? "ogg"
          : "webm";
      onFile(
        new File([blob], `voice-clone-recording.${ext}`, {
          type: mime,
          lastModified: Date.now(),
        }),
      );
    } finally {
      setSaving(false);
    }
  };

  const stopRecording = () => {
    const recorder = mediaRef.current;
    if (!recorder || recorder.state === "inactive") {
      setRecording(false);
      stopTracks();
      return;
    }
    try {
      if (recorder.state === "recording") recorder.requestData();
    } catch {
      /* ignore */
    }
    recorder.stop();
  };

  const startRecording = async () => {
    if (disabled || recording || saving || !supported) return;
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
      recorder.onstop = () => {
        void finishRecording(recorder);
      };
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
        setElapsed(Math.min(MAX_RECORD_SECONDS, seconds));
        if (seconds >= MAX_RECORD_SECONDS) {
          const active = mediaRef.current;
          if (active && active.state !== "inactive") {
            try {
              if (active.state === "recording") active.requestData();
            } catch {
              /* ignore */
            }
            active.stop();
          }
        }
      }, 200);
    } catch {
      setError(permissionDeniedLabel);
      stopTracks();
      setRecording(false);
    }
  };

  const toggleListen = async () => {
    if (!previewUrl || recording || saving || disabled) return;
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

  const progressPct = Math.min(100, (elapsed / MAX_RECORD_SECONDS) * 100);

  return (
    <div className="voice-clone-recorder">
      <p className="voice-clone-recorder-hint">{hint}</p>
      <div className="voice-clone-recorder-row">
        <button
          type="button"
          className="voice-rec-btn voice-rec-btn-start"
          disabled={disabled || recording || saving}
          onClick={() => void startRecording()}
        >
          {startLabel}
        </button>
        <button
          type="button"
          className="voice-rec-btn voice-rec-btn-stop"
          disabled={!recording}
          onClick={stopRecording}
        >
          {stopLabel}
        </button>
        <button
          type="button"
          className="voice-rec-btn voice-rec-btn-listen"
          disabled={disabled || recording || saving || !file || !previewUrl}
          onClick={() => void toggleListen()}
        >
          {playing ? listenStopLabel : listenLabel}
        </button>
        {file && !recording && !saving ? (
          <button
            type="button"
            className="voice-rec-btn voice-rec-btn-clear"
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
      <div
        className="voice-clone-meter"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={MAX_RECORD_SECONDS}
        aria-valuenow={Math.round(elapsed)}
        aria-label={recordingLabel}
      >
        <div
          className={`voice-clone-meter-fill${recording ? " is-recording" : ""}`}
          style={{ width: `${progressPct}%` }}
        />
      </div>
      <span className="voice-clone-recorder-status" aria-live="polite">
        {saving
          ? readyLabel
          : recording
            ? `${recordingLabel} ${formatClock(elapsed)} / ${formatClock(MAX_RECORD_SECONDS)}`
            : file
              ? `${readyLabel} · ${formatClock(elapsed || 0)}`
              : `${formatClock(elapsed)} / ${formatClock(MAX_RECORD_SECONDS)}`}
      </span>
      {error ? <p className="form-msg err">{error}</p> : null}
    </div>
  );
}
