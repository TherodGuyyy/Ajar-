-- Ajar database. Paste all of this into Supabase > SQL Editor and press Run.
-- Safe to run more than once.

create table if not exists public.jobs (
  fingerprint    text primary key,
  source         text not null,
  title          text not null,
  company        text not null,
  url            text not null,
  location_text  text default '',
  salary         text default '',
  score          int  not null check (score between 0 and 100),
  track          text not null,
  reasons        jsonb not null default '[]',
  warnings       jsonb not null default '[]',
  status         text not null default 'pending'
                 check (status in ('pending', 'sent', 'expired')),
  published_at   timestamptz not null,
  first_seen_at  timestamptz not null default now(),
  sent_at        timestamptz
);
create index if not exists jobs_queue on public.jobs (status, score desc);

create table if not exists public.source_state (
  source                text primary key,
  last_attempt_at       timestamptz,
  last_success_at       timestamptz,
  consecutive_failures  int not null default 0,
  last_error_code       text,
  last_count            int
);

create table if not exists public.run_log (
  id           bigint generated always as identity primary key,
  run_id       text not null,
  started_at   timestamptz not null,
  finished_at  timestamptz not null,
  status       text not null check (status in ('ok', 'partial', 'failed')),
  stats        jsonb not null default '{}'
);
create index if not exists run_log_time on public.run_log (finished_at desc);

-- Lock the tables: with Row Level Security on and no policies, public keys see nothing.
alter table public.jobs         enable row level security;
alter table public.source_state enable row level security;
alter table public.run_log      enable row level security;

-- Let only the secret (server) key use them.
grant usage on schema public to service_role;
grant select, insert, update, delete
  on public.jobs, public.source_state, public.run_log to service_role;
