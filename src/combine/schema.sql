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

-- ---------------------------------------------------------------------------
-- Training data for the residual model.
--
-- These sit BESIDE the normalized tables above rather than inside them. Those
-- are built around a canonical player_id ('josh-allen-qb-buf') that nothing in
-- the repo produces yet, and around stat lines rather than points. The training
-- set needs the opposite: platform ids, and finished per-league point totals,
-- because ESPN's own weekly projection and actual are the label and the
-- benchmark and both arrive already scored under each league's rules.
--
-- Only raw pulls are stored. Features are computed on read, because the feature
-- set will change on every modelling pass and stored features would need
-- invalidating each time.

CREATE TABLE IF NOT EXISTS espn_player_week (
  league     TEXT NOT NULL,
  season     INTEGER NOT NULL,
  week       INTEGER NOT NULL,
  espn_id    TEXT NOT NULL,
  fantasy_team TEXT NOT NULL,       -- who rostered him that week
  versus     TEXT,                  -- the fantasy team they faced
  name       TEXT NOT NULL,
  pos        TEXT NOT NULL,
  slot       TEXT NOT NULL,         -- lineup slot: a real slot, or BE/IR
  eligible   TEXT,                  -- comma separated slots he may legally fill
  started    INTEGER NOT NULL,      -- 1 when the slot was a starting slot
  team       TEXT,                  -- NFL team
  opponent   TEXT,                  -- 'vs KC' / '@ KC' / 'BYE'
  is_home    INTEGER,
  projected  REAL,                  -- ESPN's weekly projection: the benchmark
  actual     REAL,                  -- what he scored: the label
  played     INTEGER NOT NULL,
  status     TEXT,
  pulled_at  TEXT NOT NULL,
  PRIMARY KEY (league, season, week, espn_id)
);
CREATE INDEX IF NOT EXISTS idx_epw_week ON espn_player_week(season, week);
CREATE INDEX IF NOT EXISTS idx_epw_player ON espn_player_week(espn_id, season, week);

CREATE TABLE IF NOT EXISTS pff_player_week (
  season    INTEGER NOT NULL,
  week      INTEGER NOT NULL,
  pff_id    INTEGER NOT NULL,
  area      TEXT NOT NULL,          -- passing | rushing | receiving | defense
  player    TEXT,
  team      TEXT,
  position  TEXT,
  stats     TEXT NOT NULL,          -- the facet row, verbatim json
  pulled_at TEXT NOT NULL,
  PRIMARY KEY (season, week, pff_id, area)
);
CREATE INDEX IF NOT EXISTS idx_ppw_week ON pff_player_week(season, week);
CREATE INDEX IF NOT EXISTS idx_ppw_player ON pff_player_week(pff_id, season, week);

-- What the model predicted, so 2026 accuracy accumulates on live data instead
-- of resting on a backtest forever. Written whenever a prediction is shown.
CREATE TABLE IF NOT EXISTS prediction (
  league     TEXT NOT NULL,
  season     INTEGER NOT NULL,
  week       INTEGER NOT NULL,
  espn_id    TEXT NOT NULL,
  model      TEXT NOT NULL,         -- model name + version
  espn_proj  REAL,                  -- what ESPN said, for a paired comparison
  predicted  REAL NOT NULL,         -- our number
  residual   REAL,                  -- predicted minus espn_proj
  actual     REAL,                  -- filled in after the week finishes
  made_at    TEXT NOT NULL,
  PRIMARY KEY (league, season, week, espn_id, model)
);

-- Every recommendation the tool made, and what actually happened. Exists so the
-- system's claims are checkable on THIS season's live data instead of resting
-- on a backtest of last season forever. Written when advice is produced, scored
-- once the week's games are final.
--
-- Counterfactual by design: it records what the tool said, not what William
-- did. "Would this have helped" is the question it can answer honestly, and it
-- is the one that says whether the advice is worth following.
CREATE TABLE IF NOT EXISTS recommendation (
  league        TEXT NOT NULL,
  season        INTEGER NOT NULL,
  week          INTEGER NOT NULL,
  kind          TEXT NOT NULL,        -- 'start' | 'optimal' | 'waiver'
  subject_id    TEXT NOT NULL,        -- who to start, or add
  subject_name  TEXT NOT NULL,
  against_id    TEXT NOT NULL DEFAULT '',   -- who to bench, or drop
  against_name  TEXT NOT NULL DEFAULT '',
  subject_proj  REAL,                 -- the numbers AS ADVISED, never restated
  against_proj  REAL,
  edge          REAL NOT NULL,        -- projected gain when the call was made
  bar           REAL,                 -- the threshold it had to clear
  made_at       TEXT NOT NULL,
  subject_actual REAL,                -- filled in after the week is final
  against_actual REAL,
  scored_at     TEXT,
  PRIMARY KEY (league, season, week, kind, subject_id, against_id)
);
CREATE INDEX IF NOT EXISTS idx_rec_week ON recommendation(season, week);
CREATE INDEX IF NOT EXISTS idx_rec_open ON recommendation(scored_at);
