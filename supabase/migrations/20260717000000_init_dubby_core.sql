-- Dubby core schema: profiles, projects, segments, jobs, credit_ledger,
-- waitlist + RLS, indexes, triggers, and service RPC functions.
--
-- Design notes:
-- * End users reach data through the FastAPI server (service credentials),
--   which enforces ownership in every query. RLS is enabled on all tables
--   as defense in depth and to allow direct supabase-js reads later.
-- * The queue (jobs) is claimed with FOR UPDATE SKIP LOCKED; REST-backend
--   deployments use the SECURITY DEFINER functions at the bottom.

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- profiles: 1:1 with auth.users
-- ---------------------------------------------------------------------------

create table public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  email       text,
  display_name text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create trigger profiles_set_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

alter table public.profiles enable row level security;

create policy "profiles_select_own" on public.profiles
  for select using (auth.uid() = id);
create policy "profiles_update_own" on public.profiles
  for update using (auth.uid() = id) with check (auth.uid() = id);

-- ---------------------------------------------------------------------------
-- projects
-- ---------------------------------------------------------------------------

create table public.projects (
  id               uuid primary key default gen_random_uuid(),
  owner_id         uuid not null references public.profiles (id) on delete cascade,
  title            text not null check (char_length(title) between 1 and 200),
  status           text not null default 'created'
                   check (status in ('created','uploading','uploaded','processing',
                                     'ready_for_edit','dubbing','completed','failed')),
  source_lang      text not null default 'ko' check (source_lang in ('ko','en','vi')),
  target_lang      text not null default 'en' check (target_lang in ('ko','en','vi')),
  subtitle_mode    text not null default 'target'
                   check (subtitle_mode in ('none','source','target')),
  duration_seconds double precision check (duration_seconds is null or duration_seconds >= 0),
  source_key       text,
  output_key       text,
  error            text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create index projects_owner_created_idx
  on public.projects (owner_id, created_at desc);

create trigger projects_set_updated_at
  before update on public.projects
  for each row execute function public.set_updated_at();

alter table public.projects enable row level security;

create policy "projects_select_own" on public.projects
  for select using (auth.uid() = owner_id);
create policy "projects_insert_own" on public.projects
  for insert with check (auth.uid() = owner_id);
create policy "projects_update_own" on public.projects
  for update using (auth.uid() = owner_id) with check (auth.uid() = owner_id);
create policy "projects_delete_own" on public.projects
  for delete using (auth.uid() = owner_id);

-- ---------------------------------------------------------------------------
-- segments: transcript units belonging to a project
-- ---------------------------------------------------------------------------

create table public.segments (
  id          uuid primary key default gen_random_uuid(),
  project_id  uuid not null references public.projects (id) on delete cascade,
  idx         integer not null check (idx >= 0),
  start_ms    integer not null check (start_ms >= 0),
  end_ms      integer not null,
  source_text text not null default '',
  target_text text not null default '',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  constraint segments_time_order check (end_ms > start_ms),
  constraint segments_project_idx_unique unique (project_id, idx)
);

create index segments_project_idx on public.segments (project_id, idx);

create trigger segments_set_updated_at
  before update on public.segments
  for each row execute function public.set_updated_at();

alter table public.segments enable row level security;

create policy "segments_select_own" on public.segments
  for select using (
    exists (select 1 from public.projects p
            where p.id = project_id and p.owner_id = auth.uid())
  );
create policy "segments_update_own" on public.segments
  for update using (
    exists (select 1 from public.projects p
            where p.id = project_id and p.owner_id = auth.uid())
  ) with check (
    exists (select 1 from public.projects p
            where p.id = project_id and p.owner_id = auth.uid())
  );
-- Inserts/deletes happen only through the pipeline (service role bypasses RLS).

-- ---------------------------------------------------------------------------
-- jobs: pipeline work queue
-- ---------------------------------------------------------------------------

create table public.jobs (
  id           uuid primary key default gen_random_uuid(),
  project_id   uuid not null references public.projects (id) on delete cascade,
  kind         text not null check (kind in ('transcribe','dub')),
  status       text not null default 'queued'
               check (status in ('queued','running','completed','failed','cancelled')),
  progress     double precision not null default 0 check (progress between 0 and 1),
  message      text,
  error        text,
  started_at   timestamptz,
  finished_at  timestamptz,
  heartbeat_at timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create index jobs_project_created_idx on public.jobs (project_id, created_at desc);
-- Queue scan: only queued/running rows matter and stay tiny.
create index jobs_queue_idx on public.jobs (status, created_at)
  where status in ('queued', 'running');
-- At most one live job per project.
create unique index jobs_one_active_per_project
  on public.jobs (project_id)
  where status in ('queued', 'running');

create trigger jobs_set_updated_at
  before update on public.jobs
  for each row execute function public.set_updated_at();

alter table public.jobs enable row level security;

create policy "jobs_select_own" on public.jobs
  for select using (
    exists (select 1 from public.projects p
            where p.id = project_id and p.owner_id = auth.uid())
  );
-- Writes happen only through the API/worker (service role).

-- ---------------------------------------------------------------------------
-- credit_ledger: append-only; balance = sum(delta_minutes)
-- ---------------------------------------------------------------------------

create table public.credit_ledger (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references public.profiles (id) on delete cascade,
  delta_minutes numeric(10, 2) not null check (delta_minutes <> 0),
  reason        text not null
                check (reason in ('signup_grant','dub_job','refund','purchase','admin_adjust')),
  project_id    uuid references public.projects (id) on delete set null,
  created_at    timestamptz not null default now()
);

create index credit_ledger_user_created_idx
  on public.credit_ledger (user_id, created_at desc);

alter table public.credit_ledger enable row level security;

create policy "credit_ledger_select_own" on public.credit_ledger
  for select using (auth.uid() = user_id);
-- Append-only and server-written: no insert/update/delete policies.

-- ---------------------------------------------------------------------------
-- waitlist: public signup funnel (replaces the Pages Function KV store)
-- ---------------------------------------------------------------------------

create table public.waitlist (
  id         uuid primary key default gen_random_uuid(),
  email      text not null unique
             check (email ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
  locale     text,
  source     text,
  created_at timestamptz not null default now()
);

alter table public.waitlist enable row level security;

-- Anonymous visitors may join; nobody may read the list from the client.
create policy "waitlist_insert_public" on public.waitlist
  for insert to anon, authenticated with check (true);

-- ---------------------------------------------------------------------------
-- New-user bootstrap: profile row + signup credit grant
-- ---------------------------------------------------------------------------

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;

  insert into public.credit_ledger (user_id, delta_minutes, reason)
  values (new.id, 10, 'signup_grant');

  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- Service RPC functions (used by the API when DB_BACKEND=supabase_rest).
-- SECURITY DEFINER + explicit owner parameters; EXECUTE is revoked from
-- client roles so only the service role can call them.
-- ---------------------------------------------------------------------------

create or replace function public.credit_balance(p_user_id uuid)
returns numeric
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(sum(delta_minutes), 0)
  from public.credit_ledger
  where user_id = p_user_id;
$$;

create or replace function public.enqueue_job(
  p_owner_id uuid,
  p_project_id uuid,
  p_kind text
)
returns public.jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  v_job public.jobs;
begin
  perform 1 from public.projects
   where id = p_project_id and owner_id = p_owner_id
   for update;
  if not found then
    raise exception 'project_not_found';
  end if;

  perform 1 from public.jobs
   where project_id = p_project_id and status in ('queued', 'running');
  if found then
    raise exception 'active_job_exists';
  end if;

  insert into public.jobs (project_id, kind)
  values (p_project_id, p_kind)
  returning * into v_job;
  return v_job;
end;
$$;

create or replace function public.claim_next_job()
returns setof public.jobs
language sql
security definer
set search_path = public
as $$
  update public.jobs
     set status = 'running', started_at = now(), heartbeat_at = now()
   where id = (
     select id from public.jobs
      where status = 'queued'
      order by created_at
      for update skip locked
      limit 1
   )
  returning *;
$$;

create or replace function public.fail_stale_jobs(p_timeout_seconds integer)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
begin
  update public.jobs
     set status = 'failed', error = 'worker timeout', finished_at = now()
   where status = 'running'
     and heartbeat_at < now() - make_interval(secs => p_timeout_seconds);
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.update_segment_texts(
  p_owner_id uuid,
  p_project_id uuid,
  p_updates jsonb
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
begin
  with input as (
    select (elem->>'id')::uuid as id, elem->>'target_text' as target_text
    from jsonb_array_elements(p_updates) as elem
  ),
  updated as (
    update public.segments s
       set target_text = input.target_text
      from input, public.projects p
     where s.id = input.id
       and s.project_id = p_project_id
       and p.id = s.project_id
       and p.owner_id = p_owner_id
    returning s.id
  )
  select count(*) into v_count from updated;
  return v_count;
end;
$$;

-- Functions default to EXECUTE for PUBLIC; strip that too, not just the
-- Supabase client roles, so only service_role (and owners) can call them.
revoke execute on function public.enqueue_job(uuid, uuid, text) from public, anon, authenticated;
revoke execute on function public.claim_next_job() from public, anon, authenticated;
revoke execute on function public.fail_stale_jobs(integer) from public, anon, authenticated;
revoke execute on function public.update_segment_texts(uuid, uuid, jsonb) from public, anon, authenticated;
revoke execute on function public.credit_balance(uuid) from public, anon, authenticated;
