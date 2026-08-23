-- Align projects.tone_style with the 7 emotion tones used by the UI / TTS pipeline.
-- Previous check only allowed legacy: neutral, warm, energetic, serious.

alter table public.projects
  drop constraint if exists projects_tone_style_check;

alter table public.projects
  add constraint projects_tone_style_check
  check (tone_style in (
    'sad',
    'angry',
    'whisper',
    'excited',
    'energetic',
    'calm',
    'cheerful',
    -- Legacy values still present on older rows / clients
    'neutral',
    'warm',
    'serious'
  ));

alter table public.projects
  alter column tone_style set default 'calm';
