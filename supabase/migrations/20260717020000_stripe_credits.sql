-- Stripe billing, immutable credit accounting, and atomic job charging.

alter table public.jobs
  add column charged_minutes numeric(10, 2) not null default 0
  check (charged_minutes >= 0);

alter table public.credit_ledger
  add column job_id uuid references public.jobs (id) on delete set null,
  add column external_reference text,
  add column idempotency_key text;

create unique index credit_ledger_idempotency_key_idx
  on public.credit_ledger (idempotency_key)
  where idempotency_key is not null;

create unique index credit_ledger_job_debit_idx
  on public.credit_ledger (job_id)
  where reason = 'dub_job';

create unique index credit_ledger_job_refund_idx
  on public.credit_ledger (job_id)
  where reason = 'refund';

create or replace function public.prevent_credit_ledger_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception 'credit_ledger_is_immutable';
end;
$$;

create trigger credit_ledger_immutable
  before update or delete on public.credit_ledger
  for each row execute function public.prevent_credit_ledger_mutation();

create table public.stripe_customers (
  user_id uuid primary key references public.profiles (id) on delete cascade,
  stripe_customer_id text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger stripe_customers_set_updated_at
  before update on public.stripe_customers
  for each row execute function public.set_updated_at();

alter table public.stripe_customers enable row level security;
create policy "stripe_customers_select_own" on public.stripe_customers
  for select using (auth.uid() = user_id);

create table public.stripe_subscriptions (
  stripe_subscription_id text primary key,
  user_id uuid not null references public.profiles (id) on delete cascade,
  stripe_customer_id text not null,
  status text not null,
  price_id text,
  current_period_end timestamptz,
  cancel_at_period_end boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index stripe_subscriptions_user_idx
  on public.stripe_subscriptions (user_id);

create trigger stripe_subscriptions_set_updated_at
  before update on public.stripe_subscriptions
  for each row execute function public.set_updated_at();

alter table public.stripe_subscriptions enable row level security;
create policy "stripe_subscriptions_select_own" on public.stripe_subscriptions
  for select using (auth.uid() = user_id);

create table public.stripe_events (
  stripe_event_id text primary key,
  event_type text not null,
  payload jsonb not null,
  processed_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

alter table public.stripe_events enable row level security;
-- Service-only: no client policies.

create or replace function public.enqueue_job_with_credit(
  p_owner_id uuid,
  p_project_id uuid,
  p_kind text,
  p_charge_minutes numeric
)
returns public.jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  v_job public.jobs;
  v_balance numeric;
begin
  perform 1 from public.projects
   where id = p_project_id and owner_id = p_owner_id
   for update;
  if not found then
    raise exception 'project_not_found';
  end if;

  if exists (
    select 1 from public.jobs
     where project_id = p_project_id and status in ('queued', 'running')
  ) then
    raise exception 'active_job_exists';
  end if;

  if p_charge_minutes < 0 then
    raise exception 'invalid_charge';
  end if;

  if p_charge_minutes > 0 then
    -- The profile row is the per-user serialization lock for balance changes.
    perform 1 from public.profiles where id = p_owner_id for update;
    select coalesce(sum(delta_minutes), 0) into v_balance
      from public.credit_ledger where user_id = p_owner_id;
    if v_balance < p_charge_minutes then
      raise exception 'insufficient_credits';
    end if;
  end if;

  insert into public.jobs (project_id, kind, charged_minutes)
  values (p_project_id, p_kind, p_charge_minutes)
  returning * into v_job;

  if p_charge_minutes > 0 then
    insert into public.credit_ledger
      (user_id, delta_minutes, reason, project_id, job_id, idempotency_key)
    values
      (p_owner_id, -p_charge_minutes, 'dub_job', p_project_id, v_job.id,
       'job:' || v_job.id || ':debit');
  end if;
  return v_job;
end;
$$;

create or replace function public.finish_job_with_refund(
  p_job_id uuid,
  p_status text,
  p_error text default null,
  p_progress double precision default null
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_job public.jobs;
  v_owner_id uuid;
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found then return; end if;
  if v_job.status not in ('queued', 'running') then return; end if;

  update public.jobs
     set status = p_status,
         error = p_error,
         progress = coalesce(p_progress, progress),
         finished_at = now()
   where id = p_job_id;

  if p_status in ('failed', 'cancelled') and v_job.charged_minutes > 0 then
    select owner_id into v_owner_id
      from public.projects where id = v_job.project_id;
    insert into public.credit_ledger
      (user_id, delta_minutes, reason, project_id, job_id, idempotency_key)
    values
      (v_owner_id, v_job.charged_minutes, 'refund', v_job.project_id, v_job.id,
       'job:' || v_job.id || ':refund')
    on conflict (idempotency_key) where idempotency_key is not null do nothing;
  end if;
end;
$$;

create or replace function public.process_stripe_event(
  p_event_id text,
  p_event_type text,
  p_payload jsonb,
  p_user_id uuid default null,
  p_customer_id text default null,
  p_subscription_id text default null,
  p_status text default null,
  p_price_id text default null,
  p_period_end timestamptz default null,
  p_cancel_at_period_end boolean default false,
  p_credit_minutes numeric default 0,
  p_credit_reference text default null
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.stripe_events (stripe_event_id, event_type, payload)
  values (p_event_id, p_event_type, p_payload)
  on conflict do nothing;
  if not found then return false; end if;

  if p_user_id is not null and p_customer_id is not null then
    insert into public.stripe_customers (user_id, stripe_customer_id)
    values (p_user_id, p_customer_id)
    on conflict (user_id) do update
      set stripe_customer_id = excluded.stripe_customer_id;
  end if;

  if p_subscription_id is not null and p_user_id is not null then
    insert into public.stripe_subscriptions
      (stripe_subscription_id, user_id, stripe_customer_id, status, price_id,
       current_period_end, cancel_at_period_end)
    values
      (p_subscription_id, p_user_id, coalesce(p_customer_id, ''), coalesce(p_status, 'unknown'),
       p_price_id, p_period_end, p_cancel_at_period_end)
    on conflict (stripe_subscription_id) do update set
      status = excluded.status,
      price_id = excluded.price_id,
      current_period_end = excluded.current_period_end,
      cancel_at_period_end = excluded.cancel_at_period_end;
  end if;

  if p_credit_minutes > 0 and p_user_id is not null then
    perform 1 from public.profiles where id = p_user_id for update;
    insert into public.credit_ledger
      (user_id, delta_minutes, reason, external_reference, idempotency_key)
    values
      (p_user_id, p_credit_minutes, 'purchase', p_credit_reference,
       'stripe-credit:' || p_credit_reference)
    on conflict (idempotency_key) where idempotency_key is not null do nothing;
  end if;
  return true;
end;
$$;

create or replace function public.fail_stale_jobs(p_timeout_seconds integer)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_id uuid;
  v_count integer := 0;
begin
  for v_id in
    select id from public.jobs
     where status = 'running'
       and heartbeat_at < now() - make_interval(secs => p_timeout_seconds)
     for update skip locked
  loop
    perform public.finish_job_with_refund(
      v_id, 'failed', 'worker timeout', null
    );
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$$;

revoke execute on function public.enqueue_job_with_credit(uuid, uuid, text, numeric)
  from public, anon, authenticated;
revoke execute on function public.finish_job_with_refund(uuid, text, text, double precision)
  from public, anon, authenticated;
revoke execute on function public.process_stripe_event(
  text, text, jsonb, uuid, text, text, text, text, timestamptz, boolean, numeric, text
) from public, anon, authenticated;
