-- Project-level emotion mode: 사용자 지정 (custom) vs 자동감지 (auto).
-- Full emotion_tag JSON still lives on the R2 sidecar; this column is optional.

alter table public.projects
  add column if not exists emotion_mode text not null default 'auto';

alter table public.projects
  drop constraint if exists projects_emotion_mode_check;

alter table public.projects
  add constraint projects_emotion_mode_check
  check (emotion_mode in ('custom', 'auto'));

-- Allow 13-class names on tone_style (사용자 지정 fallback).
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
    'happy',
    'confident',
    'hopeful',
    'nervous',
    'serious',
    'playful',
    'worried',
    'embarrassed',
    'lonely',
    'neutral',
    'warm'
  ));
