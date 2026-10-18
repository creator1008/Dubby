# Dubby versions

| Version | Git tag | Notes |
|---------|---------|--------|
| **1.0** | `v1.0.0` | Pre-redesign pipeline: My Voice Box TTS, Demucs selective bed, full-job dub. Frozen at tag `v1.0.0`. |
| **2.0** | `v2.0.0` | Whisper-1 STT, gpt-4o-mini translation, ElevenLabs Flash TTS, selective Demucs mix. Frozen at tag `v2.0.0`. |
| **3.0** | (main) | Gemini 3.7 Flash full-document STT + spoken translation + timestamps/speakers, ElevenLabs v4 TTS. `3.0.23` fetches YouTube from a home/residential egress when Lightsail is bot-gated (`YTDLP_HOME_FETCH_URL` / `YTDLP_PROXY`). `3.0.22` dubs sources ≥5 min in ~3 min utterance-safe windows (cut after the line ends) then concatenates PCM and muxes once so 지영-length jobs fit 6GB workers. `3.0.21` aligns new-dub emotion picks with the subtitle editor’s 13 ElevenLabs classes, drops free-text tone input, and shows auto-applied TTS emotion as AI감정톤. `3.0.20` ffmpeg-slices long audio into 15s files before Demucs so 지영-length tracks survive 6GB workers. `3.0.19` runs Demucs in 7s chunks with one job so 40–60 minute dubs are not SIGKILL'd. `3.0.18` raises the source duration cap to 60 minutes. `3.0.17` re-listens 0.8s+ STT holes, retranslates leftover source-script lines so captions/TTS stay in the dubbed language, and adds 사용자 지정/자동감지 emotion-tone UX. `3.0.16` keeps Vietnamese mid-sentence question tails on the same speaker and floors quiet/whisper TTS to an audible dialogue level. `3.0.15` keeps Vietnamese mid-sentence question tails on the same speaker and floors quiet/whisper TTS to an audible dialogue level. `3.0.14` adds per-segment emotion from the same Gemini STT call (PCM heuristic fallback) and sends documented v4 audio tags. `3.0.13` keeps brief second speakers on short clips, does not drop mid-caption time when diarize has holes, and re-listens middle ASR gaps (not only the tail). |

Restore V2 tree:

```bash
git checkout v2.0.0
```
