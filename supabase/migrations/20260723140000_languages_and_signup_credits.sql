-- Expand project language codes and raise signup credit grant to 30 minutes.

alter table public.projects
  drop constraint if exists projects_source_lang_check;

alter table public.projects
  drop constraint if exists projects_target_lang_check;

alter table public.projects
  add constraint projects_source_lang_check
  check (source_lang in (
    'en','zh','ja','es','fr','pt','de','ru','ar','ur','id','ms','tr','ta','ko','vi'
  ));

alter table public.projects
  add constraint projects_target_lang_check
  check (target_lang in (
    'en','zh','ja','es','fr','pt','de','ru','ar','ur','id','ms','tr','ta','ko','vi'
  ));

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
      new.raw_user_meta_data->>'user_name',
      split_part(coalesce(new.email, ''), '@', 1)
    ),
    new.raw_user_meta_data->>'country',
    coalesce(new.raw_app_meta_data->>'provider', 'email'),
    now()
  )
  on conflict (id) do update set
    email = excluded.email,
    display_name = coalesce(public.profiles.display_name, excluded.display_name),
    auth_provider = coalesce(excluded.auth_provider, public.profiles.auth_provider),
    last_login_at = now();

  insert into public.credit_ledger (user_id, delta_minutes, reason)
  values (new.id, 30, 'signup_grant')
  on conflict do nothing;

  return new;
end;
$$;
