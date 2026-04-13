-- =============================================
-- stock-trader Supabase 스키마
-- Supabase 대시보드 → SQL Editor에 붙여넣기
-- =============================================

-- 보유 포지션
create table if not exists positions (
  ticker    text primary key,
  name      text not null default '',
  buy_price numeric not null,
  qty       integer not null,
  tp        numeric not null,   -- 익절가
  sl        numeric not null,   -- 손절가
  buy_date  date not null,
  created_at timestamptz default now()
);

-- 오늘의 매수 후보 (08:50 신호 생성 결과)
create table if not exists watchlist (
  id          bigint generated always as identity primary key,
  ticker      text not null,
  name        text not null default '',
  vol_ratio   numeric,
  day_return  numeric,
  signal_date date not null default current_date,
  created_at  timestamptz default now()
);

-- 당일 상태 (daily_pnl, initial_cash, bot_active)
create table if not exists trading_meta (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz default now()
);

-- Row Level Security 비활성화 (서버 전용 서비스키 사용)
alter table positions    disable row level security;
alter table watchlist    disable row level security;
alter table trading_meta disable row level security;


-- =============================================
-- 멀티팩터 장기 포트폴리오 테이블
-- =============================================

-- 팩터 포지션 (월 리밸런싱 장기 보유)
create table if not exists factor_positions (
  ticker         text primary key,
  name           text not null default '',
  buy_price      numeric not null,
  qty            integer not null,
  buy_date       date not null,
  score          numeric,          -- 편입 당시 팩터 점수
  pbr            numeric,
  per            numeric,
  roe            numeric,
  momentum_12m   numeric,
  created_at     timestamptz default now()
);

-- 팩터 월간 선정 종목 (리밸런싱 후보)
create table if not exists factor_watchlist (
  id             bigint generated always as identity primary key,
  ticker         text not null,
  name           text not null default '',
  score          numeric,
  pbr            numeric,
  per            numeric,
  roe            numeric,
  momentum_12m   numeric,
  rebalance_date date not null default current_date,
  created_at     timestamptz default now()
);

alter table factor_positions  disable row level security;
alter table factor_watchlist  disable row level security;
