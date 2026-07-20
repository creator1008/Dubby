-- Worker pipeline support: atomic segment replacement for the
-- supabase_rest backend. The postgres backend performs the same
-- delete+insert inside a client-side transaction; PostgREST callers need a
-- single SQL function to get atomicity.

create or replace function public.replace_segments(
  p_project_id uuid,
  p_segments jsonb
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
begin
  delete from public.segments where project_id = p_project_id;

  insert into public.segments (project_id, idx, start_ms, end_ms, source_text, target_text)
  select
    p_project_id,
    (elem->>'idx')::integer,
    (elem->>'start_ms')::integer,
    (elem->>'end_ms')::integer,
    coalesce(elem->>'source_text', ''),
    coalesce(elem->>'target_text', '')
  from jsonb_array_elements(p_segments) as elem;

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke execute on function public.replace_segments(uuid, jsonb) from public, anon, authenticated;
