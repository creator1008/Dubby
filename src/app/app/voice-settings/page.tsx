"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import type {
  SharedVoice,
  UserVoice,
  VoiceFilterOptions,
} from "@/lib/ui-types";

const EMPTY_FILTERS: VoiceFilterOptions = {
  languages: [],
  accents_by_language: {},
  genders: [],
  ages: [],
  categories: [],
};

function titleCaseWords(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function TrashIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m-9 0 1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M10 11v6M14 11v6"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function VoiceSettingsPage() {
  const text = useAppDictionary();
  const [box, setBox] = useState<UserVoice[]>([]);
  const [library, setLibrary] = useState<SharedVoice[]>([]);
  const [filters, setFilters] = useState<VoiceFilterOptions>(EMPTY_FILTERS);
  const [language, setLanguage] = useState("");
  const [accent, setAccent] = useState("");
  const [category, setCategory] = useState("");
  const [gender, setGender] = useState("");
  const [age, setAge] = useState("");
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [nicknames, setNicknames] = useState<Record<string, string>>({});
  const [addingId, setAddingId] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [loadingBox, setLoadingBox] = useState(true);
  const [loadingLibrary, setLoadingLibrary] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingUrl, setPlayingUrl] = useState<string | null>(null);

  const labelGender = useCallback(
    (value: string) => {
      const key = `voiceGender_${value.toLowerCase()}`;
      return text[key] || titleCaseWords(value) || "—";
    },
    [text],
  );

  const labelAge = useCallback(
    (value: string) => {
      const key = `voiceAge_${value.toLowerCase()}`;
      return text[key] || titleCaseWords(value) || "—";
    },
    [text],
  );

  const labelCategory = useCallback(
    (value: string) => {
      if (!value) return "—";
      const key = `voiceCat_${value.toLowerCase()}`;
      return text[key] || titleCaseWords(value);
    },
    [text],
  );

  const labelLanguage = useCallback(
    (code: string) => {
      if (!code) return "—";
      const key = `voiceLang_${code.toLowerCase()}`;
      return text[key] || code;
    },
    [text],
  );

  const labelAccent = useCallback(
    (value: string) => {
      if (!value) return "—";
      const key = `voiceAccent_${value.toLowerCase().replace(/\s+/g, "_")}`;
      return text[key] || titleCaseWords(value);
    },
    [text],
  );

  const accentOptions = useMemo(() => {
    if (!language) return [];
    return filters.accents_by_language[language] || [];
  }, [filters.accents_by_language, language]);

  const savedVoiceIds = useMemo(
    () => new Set(box.map((v) => v.shared_voice_id)),
    [box],
  );

  const loadBox = useCallback(async () => {
    setLoadingBox(true);
    try {
      const rows = await api.voices.box.list();
      setBox(rows);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : text.voiceLoadError);
    } finally {
      setLoadingBox(false);
    }
  }, [text.voiceLoadError]);

  const loadLibrary = useCallback(
    async (nextPage: number, replace: boolean) => {
      setLoadingLibrary(true);
      setMsg(null);
      try {
        const result = await api.voices.library({
          page: nextPage,
          page_size: 24,
          language: language || undefined,
          accent: language && accent ? accent : undefined,
          category: category || undefined,
          gender: gender || undefined,
          age: age || undefined,
        });
        setLibrary((prev) =>
          replace ? result.voices : [...prev, ...result.voices],
        );
        setHasMore(result.has_more);
        setPage(result.page);
      } catch (err) {
        setMsg(err instanceof Error ? err.message : text.voiceLoadError);
      } finally {
        setLoadingLibrary(false);
      }
    },
    [accent, age, category, gender, language, text.voiceLoadError],
  );

  useEffect(() => {
    void api.voices
      .filters()
      .then(setFilters)
      .catch((err: Error) => setMsg(err.message));
    void loadBox();
  }, [loadBox]);

  useEffect(() => {
    void loadLibrary(0, true);
  }, [loadLibrary]);

  useEffect(() => {
    return () => {
      audioRef.current?.pause();
    };
  }, []);

  const preview = (url: string | null | undefined) => {
    if (!url) {
      setMsg(text.voicePreviewMissing);
      return;
    }
    if (!audioRef.current) {
      audioRef.current = new Audio();
      audioRef.current.addEventListener("ended", () => setPlayingUrl(null));
      audioRef.current.addEventListener("pause", () => {
        if (audioRef.current?.ended) return;
        setPlayingUrl(null);
      });
    }
    const audio = audioRef.current;
    if (playingUrl === url && !audio.paused) {
      audio.pause();
      setPlayingUrl(null);
      return;
    }
    audio.src = url;
    void audio
      .play()
      .then(() => setPlayingUrl(url))
      .catch(() => {
        setMsg(text.voicePreviewFailed);
        setPlayingUrl(null);
      });
  };

  const setNickname = (voiceId: string, value: string) => {
    setNicknames((prev) => ({
      ...prev,
      [voiceId]: value.slice(0, 30),
    }));
  };

  const addToBox = async (voice: SharedVoice) => {
    const nickname = (nicknames[voice.voice_id] || "").trim();
    if (!nickname) {
      setMsg(text.voiceNicknameRequired);
      return;
    }
    setAddingId(voice.voice_id);
    setMsg(null);
    try {
      const saved = await api.voices.box.add({
        voice_id: voice.voice_id,
        public_owner_id: voice.public_owner_id,
        nickname,
        name: voice.name,
        description: voice.description,
        gender: voice.gender,
        accent: voice.accent,
        category: voice.category,
        language: voice.language || language || "",
        age: voice.age,
        preview_url: voice.preview_url,
      });
      setBox((prev) => [saved, ...prev.filter((v) => v.id !== saved.id)]);
      setNicknames((prev) => {
        const next = { ...prev };
        delete next[voice.voice_id];
        return next;
      });
    } catch (err) {
      setMsg(err instanceof Error ? err.message : text.voiceAddFailed);
    } finally {
      setAddingId(null);
    }
  };

  const removeFromBox = async (id: string) => {
    setRemovingId(id);
    setMsg(null);
    try {
      await api.voices.box.remove(id);
      setBox((prev) => prev.filter((v) => v.id !== id));
    } catch (err) {
      setMsg(err instanceof Error ? err.message : text.voiceRemoveFailed);
    } finally {
      setRemovingId(null);
    }
  };

  return (
    <div className="app-panel voice-settings">
      <h1 className="panel-inline-title">{text.voiceSetting}</h1>
      <p className="voice-settings-lead">{text.voiceSettingDescription}</p>
      {msg && (
        <p className="form-error" role="alert">
          {msg}
        </p>
      )}

      <section
        className="voice-section voice-section-panel"
        aria-labelledby="my-voice-box-title"
      >
        <h2 id="my-voice-box-title">{text.myVoiceBox}</h2>
        {loadingBox ? (
          <p className="muted">{text.loading}</p>
        ) : box.length === 0 ? (
          <p className="muted">{text.voiceBoxEmpty}</p>
        ) : (
          <ul className="voice-card-grid">
            {box.map((voice) => (
              <li key={voice.id} className="voice-card">
                <div className="voice-card-head">
                  <strong>{voice.nickname}</strong>
                  <div className="voice-icon-actions">
                    <button
                      type="button"
                      className="voice-icon-btn"
                      disabled={!voice.preview_url}
                      aria-label={
                        playingUrl && playingUrl === voice.preview_url
                          ? text.voicePreviewStop
                          : text.voicePreview
                      }
                      title={
                        playingUrl && playingUrl === voice.preview_url
                          ? text.voicePreviewStop
                          : text.voicePreview
                      }
                      onClick={() => preview(voice.preview_url)}
                    >
                      {playingUrl && playingUrl === voice.preview_url
                        ? "⏹"
                        : "▶"}
                    </button>
                    <button
                      type="button"
                      className="voice-icon-btn danger"
                      disabled={removingId === voice.id}
                      aria-label={text.voiceRemove}
                      title={text.voiceRemove}
                      onClick={() => void removeFromBox(voice.id)}
                    >
                      <TrashIcon />
                    </button>
                  </div>
                </div>
                <div className="voice-chip-row">
                  {voice.language ? (
                    <span className="voice-chip">
                      {labelLanguage(voice.language)}
                    </span>
                  ) : null}
                  {voice.gender ? (
                    <span className="voice-chip">
                      {labelGender(voice.gender)}
                    </span>
                  ) : null}
                  {voice.age ? (
                    <span className="voice-chip">{labelAge(voice.age)}</span>
                  ) : null}
                  {voice.accent ? (
                    <span className="voice-chip">
                      {labelAccent(voice.accent)}
                    </span>
                  ) : null}
                  {voice.category ? (
                    <span className="voice-chip">
                      {labelCategory(voice.category)}
                    </span>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section
        className="voice-section voice-section-panel"
        aria-labelledby="voice-library-title"
      >
        <h2 id="voice-library-title">{text.voiceLibrary}</h2>

        <div className="voice-filters">
          <label>
            <span>{text.voiceLanguage}</span>
            <select
              value={language}
              onChange={(e) => {
                setLanguage(e.target.value);
                setAccent("");
              }}
            >
              <option value="">{text.voiceFilterAll}</option>
              {filters.languages.map((code) => (
                <option key={code} value={code}>
                  {labelLanguage(code)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{text.voiceAccent}</span>
            <select
              value={accent}
              disabled={!language}
              onChange={(e) => setAccent(e.target.value)}
            >
              <option value="">{text.voiceFilterAll}</option>
              {accentOptions.map((item) => (
                <option key={item} value={item}>
                  {labelAccent(item)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{text.voiceGender}</span>
            <select value={gender} onChange={(e) => setGender(e.target.value)}>
              <option value="">{text.voiceFilterAll}</option>
              {filters.genders.map((item) => (
                <option key={item} value={item}>
                  {labelGender(item)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{text.voiceAge}</span>
            <select value={age} onChange={(e) => setAge(e.target.value)}>
              <option value="">{text.voiceFilterAll}</option>
              {filters.ages.map((item) => (
                <option key={item} value={item}>
                  {labelAge(item)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{text.voiceCategory}</span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              <option value="">{text.voiceFilterAll}</option>
              {filters.categories.map((item) => (
                <option key={item} value={item}>
                  {labelCategory(item)}
                </option>
              ))}
            </select>
          </label>
        </div>

        {loadingLibrary && library.length === 0 ? (
          <p className="muted">{text.loading}</p>
        ) : library.length === 0 ? (
          <p className="muted">{text.voiceLibraryEmpty}</p>
        ) : (
          <ul className="voice-card-grid voice-library-grid">
            {library.map((voice) => {
              const nickname = nicknames[voice.voice_id] || "";
              const already = savedVoiceIds.has(voice.voice_id);
              const canAdd = nickname.trim().length > 0 && !already;
              const playing =
                !!voice.preview_url && playingUrl === voice.preview_url;
              return (
                <li
                  key={`${voice.public_owner_id}-${voice.voice_id}`}
                  className={`voice-card${already ? " is-added" : ""}`}
                >
                  <div className="voice-card-head">
                    <strong>{voice.name}</strong>
                    <div className="voice-icon-actions">
                      <button
                        type="button"
                        className="voice-icon-btn"
                        disabled={!voice.preview_url}
                        aria-label={
                          playing ? text.voicePreviewStop : text.voicePreview
                        }
                        title={
                          playing ? text.voicePreviewStop : text.voicePreview
                        }
                        onClick={() => preview(voice.preview_url)}
                      >
                        {playing ? "⏹" : "▶"}
                      </button>
                      <button
                        type="button"
                        className="voice-icon-btn primary"
                        disabled={!canAdd || addingId === voice.voice_id}
                        aria-label={
                          already
                            ? text.voiceAlreadyAdded
                            : text.voiceAddToAccount
                        }
                        title={
                          already
                            ? text.voiceAlreadyAdded
                            : text.voiceAddToAccount
                        }
                        onClick={() => void addToBox(voice)}
                      >
                        {already ? "✓" : "+"}
                      </button>
                    </div>
                  </div>
                  <div className="voice-chip-row">
                    {voice.gender ? (
                      <span className="voice-chip">
                        {labelGender(voice.gender)}
                      </span>
                    ) : null}
                    {voice.age ? (
                      <span className="voice-chip">{labelAge(voice.age)}</span>
                    ) : null}
                    {voice.language ? (
                      <span className="voice-chip">
                        {labelLanguage(voice.language)}
                      </span>
                    ) : null}
                    {voice.accent ? (
                      <span className="voice-chip">
                        {labelAccent(voice.accent)}
                      </span>
                    ) : null}
                    {voice.category ? (
                      <span className="voice-chip">
                        {labelCategory(voice.category)}
                      </span>
                    ) : null}
                  </div>
                  <label className="voice-nickname-field">
                    <span className="voice-nickname-label">
                      <span>{text.voiceNickname}</span>
                      <small>{nickname.length}/30</small>
                    </span>
                    <input
                      type="text"
                      maxLength={30}
                      value={nickname}
                      disabled={already}
                      placeholder={text.voiceNicknamePlaceholder}
                      onChange={(e) =>
                        setNickname(voice.voice_id, e.target.value)
                      }
                    />
                  </label>
                </li>
              );
            })}
          </ul>
        )}

        {hasMore && (
          <div className="voice-load-more">
            <button
              type="button"
              className="btn-ghost"
              disabled={loadingLibrary}
              onClick={() => void loadLibrary(page + 1, false)}
            >
              {text.voiceLoadMore}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
