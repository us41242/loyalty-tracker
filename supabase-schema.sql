-- ============================================================================
-- Casino Rewards Scraper — Supabase Schema
-- ============================================================================

-- ── Caesars Daily Snapshots ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS caesars_rewards_snapshots (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  scraped_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  reward_credits      INTEGER,
  tier_credits        INTEGER,
  tier_status         TEXT,
  tier_next           TEXT,
  tier_credits_needed INTEGER,
  last_earned_date    DATE,
  credits_expire_date DATE,
  great_gift_points   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_caesars_snapshots_date ON caesars_rewards_snapshots (scraped_at DESC);

-- ── Caesars Reservations ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS caesars_reservations (
  id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  confirmation_code TEXT NOT NULL UNIQUE,
  property          TEXT,
  location          TEXT,
  check_in          DATE,
  check_out         DATE,
  adults            INTEGER,
  children          INTEGER,
  status            TEXT,
  room_type         TEXT,
  bed_config        TEXT,
  sq_ft             INTEGER,
  view              TEXT,
  deposit_amount    NUMERIC(10,2),
  card_last4        TEXT,
  tab               TEXT DEFAULT 'past',
  scraped_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_caesars_reservations_checkin ON caesars_reservations (check_in DESC);

-- ── Caesars Offers ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS caesars_offers (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  offer_id            TEXT NOT NULL UNIQUE,
  title               TEXT,
  description         TEXT,
  section             TEXT,
  eligible_properties TEXT,
  valid_start         DATE,
  valid_end           DATE,
  expires_at          DATE,
  first_seen          TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_caesars_offers_expires ON caesars_offers (expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_caesars_offers_offer_id ON caesars_offers (offer_id);

-- ── MGM Daily Snapshots ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mgm_rewards_snapshots (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  scraped_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  tier_status         TEXT,
  tier_credits        INTEGER,
  tier_credits_to_next INTEGER,
  tier_next           TEXT,
  rewards_points      INTEGER,
  rewards_comps_value NUMERIC(10,2),
  freeplay            NUMERIC(10,2),
  slot_dollars        NUMERIC(10,2),
  holiday_gift_points NUMERIC(10,1),
  milestone_rewards   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_mgm_snapshots_date ON mgm_rewards_snapshots (scraped_at DESC);

-- ── MGM Trips ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mgm_trips (
  id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  confirmation_code TEXT NOT NULL UNIQUE,
  property          TEXT,
  check_in          DATE,
  check_out         DATE,
  status            TEXT,
  tab               TEXT DEFAULT 'past',
  scraped_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mgm_trips_checkin ON mgm_trips (check_in DESC);

-- ── Rio Daily Snapshots ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rio_rewards_snapshots (
  id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  scraped_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  tier_status           TEXT,
  member_number         TEXT,
  points_balance        INTEGER,
  resort_credit         NUMERIC(10,2),
  points_earned_year    INTEGER,
  points_to_next_tier   INTEGER,
  next_tier             TEXT,
  status_valid_through  DATE
);

CREATE INDEX IF NOT EXISTS idx_rio_snapshots_date ON rio_rewards_snapshots (scraped_at DESC);

-- ── Rio Offers ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rio_offers (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  title       TEXT NOT NULL,
  description TEXT,
  valid_start DATE,
  valid_end   DATE,
  first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(title, valid_end)
);

CREATE INDEX IF NOT EXISTS idx_rio_offers_valid ON rio_offers (valid_end DESC);
