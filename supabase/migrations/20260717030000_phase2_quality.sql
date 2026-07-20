-- Phase 2 quality: style, diarization, quality warnings, premium lip sync.

alter table public.projects
  add column tone_style text not null default 'neutral'
    check (tone_style in ('neutral','warm','energetic','serious')),
  add column diarization_enabled boolean not null default false,
  add column quality_warnings jsonb not null default '[]'::jsonb
    check (jsonb_typeof(quality_warnings) = 'array'),
  add column lipsync_output_key text;

alter table public.segments
  add column speaker_id text,
  add column speaker_overlap boolean not null default false;

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs
  add constraint jobs_kind_check check (kind in ('transcribe','dub','lipsync'));

alter table public.credit_ledger drop constraint if exists credit_ledger_reason_check;
alter table public.credit_ledger
  add constraint credit_ledger_reason_check check (
    reason in ('signup_grant','dub_job','lipsync_job','refund','purchase','admin_adjust')
  );

create unique index credit_ledger_lipsync_job_debit_idx
  on public.credit_ledger (job_id) where reason = 'lipsync_job';

create or replace function public.replace_segments(
  p_project_id uuid,
  p_segments jsonb
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare v_count integer;
begin
  delete from public.segments where project_id = p_project_id;
  insert into public.segments
    (project_id, idx, start_ms, end_ms, source_text, target_text,
     speaker_id, speaker_overlap)
  select p_project_id, (e->>'idx')::integer, (e->>'start_ms')::integer,
    (e->>'end_ms')::integer, coalesce(e->>'source_text', ''),
    coalesce(e->>'target_text', ''), nullif(e->>'speaker_id', ''),
    coalesce((e->>'speaker_overlap')::boolean, false)
  from jsonb_array_elements(p_segments) e;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

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
declare v_job public.jobs; v_balance numeric; v_reason text;
begin
  perform 1 from public.projects
    where id = p_project_id and owner_id = p_owner_id for update;
  if not found then raise exception 'project_not_found'; end if;
  if p_kind not in ('transcribe','dub','lipsync') then
    raise exception 'invalid_job_kind';
  end if;
  if exists (select 1 from public.jobs where project_id = p_project_id
             and status in ('queued','running')) then
    raise exception 'active_job_exists';
  end if;
  if p_charge_minutes < 0 then raise exception 'invalid_charge'; end if;
  if p_charge_minutes > 0 then
    perform 1 from public.profiles where id = p_owner_id for update;
    select coalesce(sum(delta_minutes), 0) into v_balance
      from public.credit_ledger where user_id = p_owner_id;
    if v_balance < p_charge_minutes then raise exception 'insufficient_credits'; end if;
  end if;
  insert into public.jobs (project_id, kind, charged_minutes)
    values (p_project_id, p_kind, p_charge_minutes) returning * into v_job;
  if p_charge_minutes > 0 then
    v_reason := case when p_kind = 'lipsync' then 'lipsync_job' else 'dub_job' end;
    insert into public.credit_ledger
      (user_id, delta_minutes, reason, project_id, job_id, idempotency_key)
    values (p_owner_id, -p_charge_minutes, v_reason, p_project_id, v_job.id,
            'job:' || v_job.id || ':debit');
  end if;
  return v_job;
end;
$$;

revoke execute on function public.replace_segments(uuid, jsonb)
  from public, anon, authenticated;
revoke execute on function public.enqueue_job_with_credit(uuid, uuid, text, numeric)
  from public, anon, authenticated;
