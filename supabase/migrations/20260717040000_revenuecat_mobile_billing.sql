-- RevenueCat event inbox, subscription projection, and append-only credit effects.

create table public.revenuecat_events (
  revenuecat_event_id text primary key,
  event_type text not null,
  payload jsonb not null,
  processed_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

alter table public.revenuecat_events enable row level security;
-- Service-only: webhook payloads are not exposed to clients.

create table public.revenuecat_customers (
  app_user_id text primary key,
  user_id uuid not null references public.profiles (id) on delete cascade,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index revenuecat_customers_user_idx
  on public.revenuecat_customers (user_id);

create trigger revenuecat_customers_set_updated_at
  before update on public.revenuecat_customers
  for each row execute function public.set_updated_at();

alter table public.revenuecat_customers enable row level security;
create policy "revenuecat_customers_select_own" on public.revenuecat_customers
  for select using (auth.uid() = user_id);

create table public.revenuecat_subscriptions (
  user_id uuid not null references public.profiles (id) on delete cascade,
  entitlement_id text not null,
  product_id text,
  status text not null,
  expires_at timestamptz,
  store text,
  original_transaction_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, entitlement_id)
);

create trigger revenuecat_subscriptions_set_updated_at
  before update on public.revenuecat_subscriptions
  for each row execute function public.set_updated_at();

alter table public.revenuecat_subscriptions enable row level security;
create policy "revenuecat_subscriptions_select_own"
  on public.revenuecat_subscriptions for select
  using (auth.uid() = user_id);

create or replace function public.process_revenuecat_event(
  p_event_id text,
  p_event_type text,
  p_payload jsonb,
  p_user_id uuid default null,
  p_app_user_id text default null,
  p_product_id text default null,
  p_entitlement_ids jsonb default '[]'::jsonb,
  p_transaction_id text default null,
  p_original_transaction_id text default null,
  p_status text default null,
  p_expires_at timestamptz default null,
  p_store text default null,
  p_credit_minutes numeric default 0
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_entitlement_id text;
  v_revoke_reference text;
  v_granted numeric;
begin
  insert into public.revenuecat_events
    (revenuecat_event_id, event_type, payload)
  values (p_event_id, p_event_type, p_payload)
  on conflict do nothing;
  if not found then return false; end if;

  if p_user_id is not null and p_app_user_id is not null then
    insert into public.revenuecat_customers (app_user_id, user_id)
    values (p_app_user_id, p_user_id)
    on conflict (app_user_id) do update set user_id = excluded.user_id;
  end if;

  if p_user_id is not null then
    for v_entitlement_id in
      select value from jsonb_array_elements_text(p_entitlement_ids)
    loop
      insert into public.revenuecat_subscriptions
        (user_id, entitlement_id, product_id, status, expires_at, store,
         original_transaction_id)
      values
        (p_user_id, v_entitlement_id, p_product_id, coalesce(p_status, 'unknown'),
         p_expires_at, p_store, p_original_transaction_id)
      on conflict (user_id, entitlement_id) do update set
        product_id = excluded.product_id,
        status = excluded.status,
        expires_at = excluded.expires_at,
        store = excluded.store,
        original_transaction_id = excluded.original_transaction_id;
    end loop;
  end if;

  if p_credit_minutes > 0 and p_user_id is not null and p_transaction_id is not null then
    perform 1 from public.profiles where id = p_user_id for update;
    insert into public.credit_ledger
      (user_id, delta_minutes, reason, external_reference, idempotency_key)
    values
      (p_user_id, p_credit_minutes, 'purchase', p_transaction_id,
       'revenuecat-credit:' || p_transaction_id)
    on conflict (idempotency_key) where idempotency_key is not null do nothing;
  end if;

  if p_event_type = 'REFUND' and p_user_id is not null then
    perform 1 from public.profiles where id = p_user_id for update;
    v_revoke_reference := coalesce(
      p_transaction_id, p_original_transaction_id, p_event_id
    );
    select coalesce(sum(delta_minutes), 0) into v_granted
      from public.credit_ledger
     where user_id = p_user_id
       and delta_minutes > 0
       and reason = 'purchase'
       and external_reference in (p_transaction_id, p_original_transaction_id);
    if v_granted > 0 then
      insert into public.credit_ledger
        (user_id, delta_minutes, reason, external_reference, idempotency_key)
      values
        (p_user_id, -v_granted, 'refund', v_revoke_reference,
         'revenuecat-revoke:' || v_revoke_reference)
      on conflict (idempotency_key) where idempotency_key is not null do nothing;
    end if;
  end if;

  return true;
end;
$$;

revoke execute on function public.process_revenuecat_event(
  text, text, jsonb, uuid, text, text, jsonb, text, text, text,
  timestamptz, text, numeric
) from public, anon, authenticated;
grant execute on function public.process_revenuecat_event(
  text, text, jsonb, uuid, text, text, jsonb, text, text, text,
  timestamptz, text, numeric
) to service_role;
