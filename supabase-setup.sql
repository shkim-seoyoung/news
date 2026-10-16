-- Supabase SQL Editor에서 실행. 로그인 없는 공개 키워드 관리이므로 누구나 변경 가능합니다.
create table if not exists public.news_keywords (
 id bigint generated always as identity primary key,
 keyword text not null unique check (char_length(trim(keyword)) between 1 and 60),
 created_at timestamptz not null default now()
);
alter table public.news_keywords enable row level security;
grant select,insert,delete on public.news_keywords to anon;
grant usage,select on sequence public.news_keywords_id_seq to anon;
create policy "public read keywords" on public.news_keywords for select to anon using (true);
create policy "public add keywords" on public.news_keywords for insert to anon with check (true);
create policy "public delete keywords" on public.news_keywords for delete to anon using (true);
-- 주의: 공개 API이므로 악의적인 수정/삭제 및 대량 등록을 완전히 막지 못합니다.
