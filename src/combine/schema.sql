-- The Combine. Canonical identity, normalized stat-line projections, blended output.
-- Projections store STATS, never points. Scoring is applied per league at blend time.

CREATE TABLE IF NOT EXISTS player (
  player_id   TEXT PRIMARY KEY,          -- ours, e.g. 'josh-allen-qb-buf'
  full_name   TEXT NOT NULL,
  pos         TEXT NOT NULL,
  team        TEXT,
  birthdate   TEXT
);

CREATE TABLE IF NOT EXISTS player_alias (
  source      TEXT NOT NULL,             -- 'espn' | 'yahoo' | 'own' | 'pff'
  source_id   TEXT NOT NULL,
  player_id   TEXT REFERENCES player(player_id),  -- NULL = deliberately ignored
  source_name TEXT,
  origin      TEXT NOT NULL DEFAULT 'auto',       -- 'auto' | 'override'
  PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_alias_player ON player_alias(player_id);

CREATE TABLE IF NOT EXISTS projection (
  source     TEXT NOT NULL,
  player_id  TEXT NOT NULL REFERENCES player(player_id),
  season     INTEGER NOT NULL,
  week       INTEGER NOT NULL DEFAULT 0,  -- 0 = season-long
  stat       TEXT NOT NULL,               -- 'pass_yd','rec','rush_td',...
  value      REAL NOT NULL,
  pulled_at  TEXT NOT NULL,
  PRIMARY KEY (source, player_id, season, week, stat)
);

CREATE TABLE IF NOT EXISTS actual (
  player_id TEXT NOT NULL REFERENCES player(player_id),
  season    INTEGER NOT NULL,
  week      INTEGER NOT NULL,
  stat      TEXT NOT NULL,
  value     REAL NOT NULL,
  PRIMARY KEY (player_id, season, week, stat)
);

CREATE TABLE IF NOT EXISTS league_scoring (
  league TEXT NOT NULL,
  stat   TEXT NOT NULL,
  points REAL NOT NULL,
  PRIMARY KEY (league, stat)
);

CREATE TABLE IF NOT EXISTS league_settings (
  league     TEXT PRIMARY KEY,
  payload    TEXT NOT NULL,              -- json: roster slots, playoff weeks, etc
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS value_board (
  league       TEXT NOT NULL,
  season       INTEGER NOT NULL,
  week         INTEGER NOT NULL,
  player_id    TEXT NOT NULL REFERENCES player(player_id),
  blended_pts  REAL NOT NULL,
  tier         INTEGER,
  rank_pos     INTEGER,
  rank_overall INTEGER,
  sources_used INTEGER NOT NULL,
  built_at     TEXT NOT NULL,
  PRIMARY KEY (league, season, week, player_id)
);
CREATE INDEX IF NOT EXISTS idx_vb_rank ON value_board(league, season, week, rank_overall);

CREATE TABLE IF NOT EXISTS run_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  job         TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT NOT NULL,             -- 'running' | 'ok' | 'error'
  detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_runlog_job ON run_log(job, started_at DESC);
