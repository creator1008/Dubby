-- Allow the subtitle editor to correct ASR source text alongside translations.
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
    select (elem->>'id')::uuid as id,
           elem->>'target_text' as target_text,
           elem->>'source_text' as source_text
    from jsonb_array_elements(p_updates) as elem
  ),
  updated as (
    update public.segments s
       set target_text = input.target_text,
           source_text = coalesce(input.source_text, s.source_text)
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

revoke execute on function public.update_segment_texts(uuid, uuid, jsonb) from public, anon, authenticated;
