--[[
  SILM GPS-loss failsafe  (runs ON THE FLIGHT CONTROLLER, Matek H743)

  Why this exists:
    ArduPlane already dead-reckons on airspeed + wind when GPS is lost, but it
    has no built-in "GPS lost -> go home" action. In AUTO it keeps flying the
    mission outward on a dead-reckoned position whose error grows every second.
    This script turns the aircraft for home as soon as GPS has been lost for
    SLM_GPS_LOSS_S seconds, so it spends as little time as possible flying on a
    drifting estimate and is as close to home as possible if GPS never returns.

  Behaviour:
    * Only acts when armed, flying, and in AUTO (mode 10). It never overrides a
      pilot: manual modes are left alone.
    * GPS "bad" = primary GPS has no 3D fix, OR fewer than SLM_MIN_SATS
      satellites, OR HDOP above SLM_MAX_HDOP. Under real jamming the receiver
      flickers in and out of "lock" with junk satellites, so a plain fix check
      would keep resetting the timer.
    * A loss episode starts at the first bad sample and only ends after GPS has
      been continuously good for SLM_GPS_OK_S seconds (hysteresis), so
      flickering lock during jamming cannot reset it.
    * After SLM_GPS_LOSS_S seconds of GPS being bad within the episode -> switch
      to RTL (mode 11) once per episode, and announce it (also written to the
      onboard log).
    * If GPS comes back, it announces recovery but does NOT switch back to AUTO
      automatically: after a GPS loss the safe default is to come home. The
      pilot can re-enter AUTO.
    * If the pilot changes mode after the script switched to RTL, the script
      respects that and does not fight it.

  Install: copy to the SD card at APM/scripts/silm_gps_loss.lua, set
  SCR_ENABLE=1, reboot, then reboot once more so the SLM_ params appear.

  Parameters (created by this script):
    SLM_ENABLE      1 = active, 0 = disabled
    SLM_GPS_LOSS_S  seconds of bad GPS (within one episode) before RTL (default 5)
    SLM_GPS_OK_S    seconds of continuously good GPS that end an episode (default 10)
    SLM_MIN_SATS    minimum satellites for GPS to count as good (default 6)
    SLM_MAX_HDOP    maximum HDOP for GPS to count as good (default 2.5)
--]]

local MAV_SEVERITY = {EMERGENCY=0, ALERT=1, CRITICAL=2, ERROR=3, WARNING=4, NOTICE=5, INFO=6, DEBUG=7}
local MODE_AUTO = 10
local MODE_RTL = 11
local GPS_OK_FIX_3D = 3
local RUN_MS = 200

local PARAM_TABLE_KEY = 61
local PARAM_TABLE_PREFIX = "SLM_"
assert(param:add_table(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, 6), "SILM: could not add param table")

local function bind_add_param(name, idx, default_value)
  assert(param:add_param(PARAM_TABLE_KEY, idx, name, default_value),
         string.format("SILM: could not add param %s", name))
  local p = Parameter()
  assert(p:init(PARAM_TABLE_PREFIX .. name), "SILM: could not bind " .. name)
  return p
end

local SLM_ENABLE     = bind_add_param("ENABLE", 1, 1)
local SLM_GPS_LOSS_S = bind_add_param("GPS_LOSS_S", 2, 5)
local SLM_GPS_OK_S   = bind_add_param("GPS_OK_S", 3, 10)
local SLM_MIN_SATS   = bind_add_param("MIN_SATS", 4, 6)
local SLM_MAX_HDOP   = bind_add_param("MAX_HDOP", 5, 2.5)

local in_episode = false      -- inside a GPS-loss episode
local bad_ms = 0              -- accumulated bad-GPS time in this episode (ms)
local good_since_ms = nil     -- start of the current continuous good stretch
local last_ms = nil           -- previous update time
local acted = false           -- already switched to RTL in this episode

local function gps_good()
  local inst = gps:primary_sensor()
  if gps:status(inst) < GPS_OK_FIX_3D then return false end
  if gps:num_sats(inst) < SLM_MIN_SATS:get() then return false end
  return gps:get_hdop(inst) <= SLM_MAX_HDOP:get() * 100   -- hdop is x100
end

local function end_episode()
  in_episode = false
  bad_ms = 0
  good_since_ms = nil
  acted = false
end

local function update()
  if SLM_ENABLE:get() ~= 1 then
    end_episode()
    last_ms = nil
    return update, 1000
  end

  local now = millis()
  local dt_ms = 0
  if last_ms ~= nil then dt_ms = (now - last_ms):tofloat() end
  last_ms = now

  local good = gps_good()
  local flying = arming:is_armed() and vehicle:get_likely_flying()

  if not in_episode then
    if good or not flying then
      return update, RUN_MS
    end
    in_episode = true
    bad_ms = 0
    good_since_ms = nil
    gcs:send_text(MAV_SEVERITY.WARNING, "SILM: GPS lost/degraded, dead reckoning")
  end

  -- inside an episode
  if good then
    if good_since_ms == nil then good_since_ms = now end
    if (now - good_since_ms):tofloat() >= SLM_GPS_OK_S:get() * 1000 then
      gcs:send_text(MAV_SEVERITY.NOTICE, "SILM: GPS recovered")
      end_episode()
    end
    return update, RUN_MS
  end

  good_since_ms = nil
  if not flying then
    -- landed / disarmed during an episode: nothing to do, start fresh later
    end_episode()
    return update, RUN_MS
  end
  bad_ms = bad_ms + dt_ms

  if (not acted) and bad_ms >= SLM_GPS_LOSS_S:get() * 1000 then
    if vehicle:get_mode() == MODE_AUTO then
      if vehicle:set_mode(MODE_RTL) then
        gcs:send_text(MAV_SEVERITY.CRITICAL,
          string.format("SILM: GPS bad %.0fs -> RTL on dead reckoning", bad_ms / 1000))
      else
        gcs:send_text(MAV_SEVERITY.ERROR, "SILM: RTL mode change refused")
      end
    end
    -- Mark handled even if we were not in AUTO, so we never override a pilot
    -- who is flying manually, and never re-trigger in this episode.
    acted = true
  end

  return update, RUN_MS
end

gcs:send_text(MAV_SEVERITY.INFO, "SILM GPS-loss failsafe loaded")
return update, 1000
