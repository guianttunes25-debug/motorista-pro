-- Tabela de perfis dos clientes
create table if not exists public.profiles (
  id        uuid primary key references auth.users(id) on delete cascade,
  email     text not null,
  plano     text not null check (plano in ('basico','intermediario','premium','premium-plus')),
  criado_em timestamptz not null default now()
);

-- RLS: usuário só vê o próprio perfil
alter table public.profiles enable row level security;

create policy "Usuário lê próprio perfil"
  on public.profiles for select
  using (auth.uid() = id);

-- Função chamada automaticamente quando novo usuário é criado via Auth
-- (usada como fallback; o webhook cria o perfil diretamente)
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
  insert into public.profiles (id, email, plano)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data->>'plano', 'basico')
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

create or replace trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
