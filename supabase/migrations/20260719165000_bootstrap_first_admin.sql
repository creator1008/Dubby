-- One-time bootstrap for the initial Dubby administrator.
-- Abort instead of silently granting the wrong/missing account.
do $$
declare
  matched_users integer;
begin
  select count(*)
    into matched_users
    from auth.users
   where lower(email) = 'passionmasters@gmail.com';

  if matched_users <> 1 then
    raise exception 'Expected one initial admin account, found %', matched_users;
  end if;

  update auth.users
     set raw_app_meta_data =
         coalesce(raw_app_meta_data, '{}'::jsonb) || '{"role":"admin"}'::jsonb,
         updated_at = now()
   where lower(email) = 'passionmasters@gmail.com';
end
$$;
