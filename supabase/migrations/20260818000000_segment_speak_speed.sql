-- Persist editor speak-rate + allow end_ms updates from the subtitle editor.
alter table public.segments
  add column if not exists speak_speed double precision;

comment on column public.segments.speak_speed is
  'User/pipeline TTS speak-rate multiplier (1.0 = natural).';

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
    select
      (elem->>'id')::uuid as id,
      elem->>'target_text' as target_text,
      elem->>'source_text' as source_text,
      case
        when elem ? 'end_ms' and nullif(elem->>'end_ms', '') is not null
          then (elem->>'end_ms')::integer
        else null
      end as end_ms,
      case
        when elem ? 'speak_speed' and nullif(elem->>'speak_speed', '') is not null
          then (elem->>'speak_speed')::double precision
        else null
      end as speak_speed
    from jsonb_array_elements(p_updates) as elem
  ),
  updated as (
    update public.segments s
       set target_text = input.target_text,
           source_text = coalesce(input.source_text, s.source_text),
           end_ms = case
             when input.end_ms is not null
               and input.end_ms > s.start_ms
             then input.end_ms
             else s.end_ms
           end,
           speak_speed = coalesce(input.speak_speed, s.speak_speed)
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

revoke execute on function public.update_segment_texts(uuid, uuid, jsonb)
  from public, anon, authenticated;
