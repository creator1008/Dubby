-- Per-user Voice Box: saved ElevenLabs shared voices with nicknames.

create table if not exists public.user_voices (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users (id) on delete cascade,
  nickname text not null
    check (char_length(btrim(nickname)) >= 1 and char_length(nickname) <= 30),
  elevenlabs_voice_id text not null,
  shared_voice_id text not null,
  public_owner_id text not null default '',
  name text not null default '',
  description text not null default '',
  gender text not null default '',
  accent text not null default '',
  category text not null default '',
  language text not null default '',
  age text not null default '',
  preview_url text,
  created_at timestamptz not null default now(),
  unique (owner_id, shared_voice_id),
  unique (owner_id, nickname)
);

create index if not exists user_voices_owner_created_idx
  on public.user_voices (owner_id, created_at desc);

comment on table public.user_voices is
  'User-saved ElevenLabs shared voices (My Voice Box).';

alter table public.user_voices enable row level security;

create policy "user_voices_select_own" on public.user_voices
  for select using (auth.uid() = owner_id);

create policy "user_voices_insert_own" on public.user_voices
  for insert with check (auth.uid() = owner_id);

create policy "user_voices_update_own" on public.user_voices
  for update using (auth.uid() = owner_id);

create policy "user_voices_delete_own" on public.user_voices
  for delete using (auth.uid() = owner_id);
