-- Dubby V2.0 project voice mode + pipeline version (optional columns).
-- Applied when DATABASE_URL is available; R2 sidecar is the live fallback.

alter table public.projects
  add column if not exists voice_mode text not null default 'voice_box'
    check (voice_mode in ('voice_box', 'auto_clone'));

alter table public.projects
  add column if not exists pipeline_version text not null default '2.0';

comment on column public.projects.voice_mode is
  'voice_box = My Voice Box IDs; auto_clone = Instant Voice Clone per speaker';
comment on column public.projects.pipeline_version is
  '2.0 = original-base mix + passthrough; 1.0 = legacy Demucs selective bed';
