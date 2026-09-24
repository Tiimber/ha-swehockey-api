DOMAIN = "hockeylive"

# How often to poll, depending on current game state.
UPDATE_INTERVAL_DEMO     = 15     # seconds – demo simulation (advances every 15s)
UPDATE_INTERVAL_LIVE     = 30     # seconds – live game in progress
UPDATE_INTERVAL_GAME_DAY = 3600   # 1 hour  – game day, match not yet started
UPDATE_INTERVAL_IDLE     = 21600  # 6 hours – no game today

# The API promotes today's game to "current" this long before face-off
# (PRE_GAME_WINDOW in app.py); the coordinator wakes for that moment.
PRE_GAME_WINDOW_SECONDS = 7200

# A game that started less than this many seconds ago may still be live.
LIVE_WINDOW_SECONDS = 14400  # 4 hours
