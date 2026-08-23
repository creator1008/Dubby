-- Thai + Burmese (Myanmar) dubbing languages; keep ko/vi in the check list.
alter table public.projects
  drop constraint if exists projects_source_lang_check;

alter table public.projects
  drop constraint if exists projects_target_lang_check;

alter table public.projects
  add constraint projects_source_lang_check
  check (source_lang in (
    'ko','vi','en','zh','ja','es','fr','pt','de','ru','ar','ur','id','ms','tr','ta','th','my'
  ));

alter table public.projects
  add constraint projects_target_lang_check
  check (target_lang in (
    'ko','vi','en','zh','ja','es','fr','pt','de','ru','ar','ur','id','ms','tr','ta','th','my'
  ));
