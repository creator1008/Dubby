# Dubby versions

| Version | Git tag | Notes |
|---------|---------|--------|
| **1.0** | `v1.0.0` | Pre-redesign pipeline: My Voice Box TTS, Demucs selective bed, full-job dub. Frozen at tag `v1.0.0`. |
| **2.0** | `v2.0.0` | Whisper-1 STT, gpt-4o-mini translation, ElevenLabs Flash TTS, selective Demucs mix. Frozen at tag `v2.0.0`. |
| **3.0** | (main) | Gemini 3.7 Flash full-document STT + spoken translation + timestamps/speakers, ElevenLabs v3 TTS. `3.0.7` retries R2 multipart parts (16 MiB) so 100MB+ new-dub uploads do not fail on part 2. |

Restore V2 tree:

```bash
git checkout v2.0.0
```
