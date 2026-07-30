-- Allow ON DELETE SET NULL on credit_ledger.project_id / job_id.
-- The append-only trigger previously blocked those FK updates, so project
-- DELETE failed with credit_ledger_is_immutable whenever a debit referenced
-- the project.

create or replace function public.prevent_credit_ledger_mutation()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'credit_ledger_is_immutable';
  end if;

  -- Permit nulling FK columns when parent projects/jobs are removed.
  if new.id is not distinct from old.id
     and new.user_id is not distinct from old.user_id
     and new.delta_minutes is not distinct from old.delta_minutes
     and new.reason is not distinct from old.reason
     and new.created_at is not distinct from old.created_at
     and new.external_reference is not distinct from old.external_reference
     and new.idempotency_key is not distinct from old.idempotency_key
     and new.admin_note is not distinct from old.admin_note
     and new.adjusted_by is not distinct from old.adjusted_by
     and (new.project_id is not distinct from old.project_id or new.project_id is null)
     and (new.job_id is not distinct from old.job_id or new.job_id is null)
     and (
       new.project_id is distinct from old.project_id
       or new.job_id is distinct from old.job_id
     )
  then
    return new;
  end if;

  raise exception 'credit_ledger_is_immutable';
end;
$$;
