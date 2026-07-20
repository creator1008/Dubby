-- Social-auth profile enrichment and service-only administrator audit data.

alter table public.profiles
  add column if not exists country text,
  add column if not exists auth_provider text,
  add column if not exists last_login_at timestamptz;

alter table public.profiles
  add constraint profiles_country_format_check
  check (country is null or country ~ '^[A-Z]{2,8}$');

alter table public.credit_ledger
  add column if not exists admin_note text,
  add column if not exists adjusted_by uuid
    references public.profiles (id) on delete set null;

create table if not exists public.access_logs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.profiles (id) on delete set null,
  method text not null,
  path text not null,
  status_code integer not null,
  ip_address inet,
  user_agent text,
  created_at timestamptz not null default now()
);

create index if not exists access_logs_created_idx
  on public.access_logs (created_at desc);
create index if not exists access_logs_user_created_idx
  on public.access_logs (user_id, created_at desc);

alter table public.access_logs enable row level security;
-- No client policies: only the API service role may read or write access logs.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (
    id,
    email,
    display_name,
    country,
    auth_provider,
    last_login_at
  )
  values (
    new.id,
    new.email,
    coalesce(
      new.raw_user_meta_data->>'full_name',
      new.raw_user_meta_data->>'name',
      new.raw_user_meta_data->>'user_name'
    ),
    new.raw_user_meta_data->>'country',
    new.raw_app_meta_data->>'provider',
    now()
  )
  on conflict (id) do update set
    email = excluded.email,
    display_name = coalesce(public.profiles.display_name, excluded.display_name),
    auth_provider = coalesce(excluded.auth_provider, public.profiles.auth_provider),
    last_login_at = now();

  insert into public.credit_ledger (user_id, delta_minutes, reason)
  values (new.id, 10, 'signup_grant')
  on conflict do nothing;

  return new;
end;
$$;
