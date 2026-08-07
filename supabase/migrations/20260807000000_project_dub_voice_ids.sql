-- Ordered ElevenLabs voice IDs for dubbing (speaker 1, speaker 2, …).
alter table public.projects
  add column if not exists dub_voice_ids jsonb not null default '[]'::jsonb;

comment on column public.projects.dub_voice_ids is
  'Ordered ElevenLabs voice IDs from My Voice Box for speaker slots.';
