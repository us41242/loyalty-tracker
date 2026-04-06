require('dotenv').config();
const { Client } = require('pg');

// Try direct connection (not pooler)
const DB_URL = `postgresql://postgres:${process.env.SUPABASE_DB_PASSWORD}@db.cuzyicjsyoddbeiosuxn.supabase.co:5432/postgres`;

async function setupDatabase() {
  const client = new Client({ connectionString: DB_URL, ssl: { rejectUnauthorized: false } });

  try {
    await client.connect();
    console.log('🔌 Connected to Supabase PostgreSQL\n');

    // Drop existing tables
    console.log('🗑️  Dropping existing tables...');
    await client.query(`
      DROP TABLE IF EXISTS caesars_rewards_snapshots CASCADE;
      DROP TABLE IF EXISTS caesars_reservations CASCADE;
      DROP TABLE IF EXISTS caesars_offers CASCADE;
      DROP TABLE IF EXISTS mgm_rewards_snapshots CASCADE;
      DROP TABLE IF EXISTS mgm_trips CASCADE;
      DROP TABLE IF EXISTS rio_rewards_snapshots CASCADE;
      DROP TABLE IF EXISTS rio_offers CASCADE;
    `);
    console.log('   Done.\n');

    // Create new tables
    console.log('📦 Creating tables...');

    await client.query(`
      CREATE TABLE caesars_rewards_snapshots (
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
      CREATE INDEX idx_caesars_snapshots_date ON caesars_rewards_snapshots (scraped_at DESC);
    `);
    console.log('   ✅ caesars_rewards_snapshots');

    await client.query(`
      CREATE TABLE caesars_reservations (
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
      CREATE INDEX idx_caesars_reservations_checkin ON caesars_reservations (check_in DESC);
    `);
    console.log('   ✅ caesars_reservations');

    await client.query(`
      CREATE TABLE caesars_offers (
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
      CREATE INDEX idx_caesars_offers_expires ON caesars_offers (expires_at DESC);
      CREATE INDEX idx_caesars_offers_offer_id ON caesars_offers (offer_id);
    `);
    console.log('   ✅ caesars_offers');

    await client.query(`
      CREATE TABLE mgm_rewards_snapshots (
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
      CREATE INDEX idx_mgm_snapshots_date ON mgm_rewards_snapshots (scraped_at DESC);
    `);
    console.log('   ✅ mgm_rewards_snapshots');

    await client.query(`
      CREATE TABLE mgm_trips (
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
      CREATE INDEX idx_mgm_trips_checkin ON mgm_trips (check_in DESC);
    `);
    console.log('   ✅ mgm_trips');

    await client.query(`
      CREATE TABLE rio_rewards_snapshots (
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
      CREATE INDEX idx_rio_snapshots_date ON rio_rewards_snapshots (scraped_at DESC);
    `);
    console.log('   ✅ rio_rewards_snapshots');

    await client.query(`
      CREATE TABLE rio_offers (
        id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        title       TEXT NOT NULL,
        description TEXT,
        valid_start DATE,
        valid_end   DATE,
        first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(title, valid_start, valid_end)
      );
      CREATE INDEX idx_rio_offers_valid ON rio_offers (valid_end DESC);
    `);
    console.log('   ✅ rio_offers');

    console.log('\n🎉 Database setup complete! 7 tables created.');

  } catch (err) {
    console.error('❌ Error:', err.message);
  } finally {
    await client.end();
  }
}

setupDatabase();
