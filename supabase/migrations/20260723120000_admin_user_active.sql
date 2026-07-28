-- Admin user activation flag + indexes for admin usage queries.

alter table public.profiles
  add column if not exists is_active boolean not null default true;

alter table public.profiles
  add column if not exists deactivated_at timestamptz;

comment on column public.profiles.is_active is
  'When false, API rejects non-admin requests for this account.';

create index if not exists profiles_is_active_idx
  on public.profiles (is_active)
  where is_active = false;
