"use client";

import { useCallback, useRef, useState, type DragEvent } from "react";
import { useAppDictionary } from "@/lib/i18n/locale-context";

const MAX_BYTES = 500 * 1024 * 1024;

function formatBytes(n: number) {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

type Props = {
  file: File | null;
  onFile: (file: File | null) => void;
  disabled?: boolean;
};

export function FileUploader({ file, onFile, disabled }: Props) {
  const text = useAppDictionary();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const validate = useCallback((f: File) => {
    if (!f.type.startsWith("video/") && !f.type.startsWith("audio/")) {
      return text.invalidMedia;
    }
    if (f.size > MAX_BYTES) {
      return `${text.fileTooLarge} ${formatBytes(MAX_BYTES)}`;
    }
    return null;
  }, [text]);

  const pick = useCallback(
    (f: File | null) => {
      if (!f) {
        onFile(null);
        setLocalError(null);
        return;
      }
      const err = validate(f);
      if (err) {
        setLocalError(err);
        onFile(null);
        return;
      }
      setLocalError(null);
      onFile(f);
    },
    [onFile, validate],
  );

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const f = e.dataTransfer.files?.[0];
    if (f) pick(f);
  };

  return (
    <div className="file-uploader">
      <div
        className={`dropzone ${dragOver ? "drag-over" : ""} ${file ? "has-file" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => !disabled && inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            if (!disabled) inputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label={text.chooseMedia}
      >
        <input
          ref={inputRef}
          type="file"
          accept="video/*,audio/*"
          hidden
          disabled={disabled}
          onChange={(e) => pick(e.target.files?.[0] ?? null)}
        />
        {file ? (
          <>
            <strong>{file.name}</strong>
            <span>
              {formatBytes(file.size)} · {file.type || "unknown"}
            </span>
            <button
              type="button"
              className="file-change-btn"
              disabled={disabled}
              onClick={(e) => {
                e.stopPropagation();
                inputRef.current?.click();
              }}
            >
              {text.selectAnotherFile}
            </button>
          </>
        ) : (
          <>
            <strong>{text.dropFile}</strong>
            <span>{text.fileHint}</span>
          </>
        )}
      </div>
      {localError && <p className="form-msg err">{localError}</p>}
    </div>
  );
}
