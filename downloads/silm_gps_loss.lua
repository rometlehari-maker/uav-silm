--[[
  SILM GPS-loss failsafe  (runs ON THE FLIGHT CONTROLLER, Matek H743)

  Why this exists:
    ArduPlane already dead-reckons on airspeed + wind when GPS is lost, but it
    has no built-in "GPS lost -> go home" action. In AUTO it keeps flying the
    mission outward on a dead-reckoned position whose error grows every second.
    This script switches to RTL as soon as GPS has been lost for
    SLM_GPS_LOSS_S seconds. This aircraft has no RC link, so with
    RTL_AUTOLAND=2 (gps_denied_failsafe.param) RTL means "fly the mission's
    landing sequence (DO_LAND_START ... NAV_LAND) and land": it spends as
    little time as possible on a drifting estimate and lands instead of
    circling until the battery is empty.

  Behaviour:
    * Only acts when armed, flying, and in AUTO (mode 10). Other modes (set
      from a ground station, if one is connected) are left alone.
    * Never interrupts a landing: if the mission is already past a
      DO_LAND_START item (or the vehicle reports it is landing), it does
      nothing - RTL would restart the landing sequence from its beginning.
    * Waits for an automatic takeoff to finish before acting, rather than
      diving into the landing sequence from a few metres after launch.
    * GPS "bad" = primary GPS has no 3D fix, OR fewer than SLM_MIN_SATS
      satellites, OR HDOP above SLM_MAX_HDOP. Under real jamming the receiver
      flickers in and out of "lock" with junk satellites, so a plain fix check
      would keep resetting the timer.
    * Jammers and spoofers can make the receiver report a perfect fix (SITL's
      jamming: "RTK fixed", 10-15 satellites, HDOP 1.2 between dropouts), so
      the quality checks alone counted only the dropouts and the failsafe took
      12 s instead of 5 in strong wind. While an episode lasts (EKF on source
      set 2, i.e. NOT using GPS) a fix further than SLM_GPS_DIFF_M from the
      EKF's own position also counts as bad time. This only speeds up the RTL
      decision; recovery still uses the quality checks alone, because after a
      long blind leg the genuine GPS can legitimately be far from the drifted
      estimate.
    * A loss episode starts at the first bad sample and only ends after GPS has
      been continuously good for SLM_GPS_OK_S seconds (hysteresis), so
      flickering lock during jamming cannot reset it.
    * After SLM_GPS_LOSS_S seconds of GPS being bad within the episode -> switch
      to RTL (mode 11) once per episode, and announce it (also written to the
      onboard log).
    * While a loss episode lasts, the EKF is switched to source set 2
      (EK3_SRC2_*: no GPS position/velocity, baro height, compass yaw) so it
      dead-reckons on airspeed instead of fusing a jammed receiver's fake
      positions and vertical velocity; it is switched back to source set 1
      when the episode ends (GPS good for SLM_GPS_OK_S, or landed). Under
      jamming the receiver still reports a flickering "3D fix", and fusing
      that corrupted the height rate enough to trigger the landing flare
      tens of metres up (SITL). Needs AHRS_OPTIONS=1 so the AHRS doesn't fall
      back to DCM, which navigates on raw GPS. SLM_SRC_SW=0 disables this.
    * Arming gate: refuses to arm ("PreArm: SILM: ...") until the EKF has set
      its origin from GPS, home is set and GPS is good. AHRS_OPTIONS=1 removes
      ArduPilot's own "not using configured AHRS type" pre-arm check, and in
      SITL the aircraft then armed and launched before the EKF had GPS; the
      origin was set in the climb and every height after that was ~20 m off
      (landing flare 20 m up). This gate puts that check back, whatever arms
      the aircraft (planner button, ground station, test tool).
    * Landed without GPS -> disarm. ArduPlane disarms LAND_DISARMDELAY after it
      decides it is no longer flying, but with no GPS its estimate keeps
      "moving" at the wind speed it learnt in the air, so in SITL it stayed
      ARMED on the ground until GPS came back - minutes. So, only while the
      EKF is off GPS (an episode), the script disarms once ALL of these have
      held for SLM_LAND_DIS_S: armed, AUTO, on the mission's LAND item,
      airspeed below 80 % of the stall speed (AIRSPEED_MIN / 1.3) - impossible
      in flight for that long -, not rotating (gyros quiet) and less than 5 m
      above home (barometer).
    * If GPS comes back, it announces recovery but does NOT resume the survey:
      after a GPS loss the safe default is to land.
    * If a ground station changes the mode after the script acted, the script
      respects that and does not fight it.

  Install: copy to the SD card at APM/scripts/silm_gps_loss.lua, set
  SCR_ENABLE=1, reboot, then reboot once more so the SLM_ params appear.

  Parameters (created by this script):
    SLM_ENABLE      1 = active, 0 = disabled
    SLM_GPS_LOSS_S  seconds of bad GPS (within one episode) before RTL (default 5)
    SLM_GPS_OK_S    seconds of continuously good GPS that end an episode (default 10)
    SLM_MIN_SATS    minimum satellites for GPS to count as good (default 6)
    SLM_MAX_HDOP    maximum HDOP for GPS to count as good (default 2.5)
    SLM_SRC_SW      1 = use EKF source set 2 (no GPS) during an episode (default 1)
    SLM_LAND_DIS_S  landed without GPS: disarm after this long certainly on the
                    ground (default 15 s; 0 = off, see below)
    SLM_GPS_DIFF_M  during an episode, a GPS fix further than this from the
                    EKF's (GPS-free) position counts as bad (default 50 m; 0 = off)
--]]

local MAV_SEVERITY = {EMERGENCY=0, ALERT=1, CRITICAL=2, ERROR=3, WARNING=4, NOTICE=5, INFO=6, DEBUG=7}
local MODE_AUTO = 10
local MODE_RTL = 11
local CMD_DO_LAND_START = 189
local CMD_NAV_LAND = 21
local GPS_OK_FIX_3D = 3
local RUN_MS = 200

local PARAM_TABLE_KEY = 61
local PARAM_TABLE_PREFIX = "SLM_"
assert(param:add_table(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, 10), "SILM: could not add param table")

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
local SLM_SRC_SW     = bind_add_param("SRC_SW", 6, 1)
local SLM_GPS_DIFF_M = bind_add_param("GPS_DIFF_M", 7, 50)
local SLM_LAND_DIS_S = bind_add_param("LAND_DIS_S", 8, 15)
local AIRSPEED_MIN = Parameter()
if not AIRSPEED_MIN:init("AIRSPEED_MIN") then AIRSPEED_MIN = nil end

local SRC_SET_GPS = 0         -- EK3_SRC1_*: normal, with GPS
local SRC_SET_NO_GPS = 1      -- EK3_SRC2_*: no GPS (dead reckoning)
local on_no_gps_src = false   -- we switched the EKF to SRC_SET_NO_GPS

-- Arming gate (auxiliary arming authorisation). nil if unavailable.
local auth_id = arming:get_aux_auth_id()
local auth_state = false      -- last state sent (false = nothing sent yet; nil = passed)

local function update_arming_gate(good)
  if auth_id == nil or arming:is_armed() then return end
  local why = nil
  if SLM_ENABLE:get() == 1 then
    if not ahrs:home_is_set() then
      why = "SILM: waiting for home"
    elseif ahrs:get_origin() == nil then
      why = "SILM: waiting for EKF to use GPS"
    elseif not good then
      why = "SILM: GPS not good enough"
    end
  end
  if why == auth_state then return end
  if why == nil then
    arming:set_aux_auth_passed(auth_id)
  else
    arming:set_aux_auth_failed(auth_id, why)
  end
  auth_state = why
end

local function use_no_gps_sources(want)
  if want and SLM_SRC_SW:get() ~= 1 then want = false end
  if want == on_no_gps_src then return end
  ahrs:set_posvelyaw_source_set(want and SRC_SET_NO_GPS or SRC_SET_GPS)
  on_no_gps_src = want
  if want then
    gcs:send_text(MAV_SEVERITY.WARNING, "SILM: EKF ignoring GPS (source set 2)")
  else
    gcs:send_text(MAV_SEVERITY.NOTICE, "SILM: EKF using GPS again (source set 1)")
  end
end

local in_episode = false      -- inside a GPS-loss episode
local bad_ms = 0              -- accumulated bad-GPS time in this episode (ms)
local good_since_ms = nil     -- start of the current continuous good stretch
local last_ms = nil           -- previous update time
local acted = false           -- already switched to RTL in this episode

-- true if the GPS fix is far from the EKF position while the EKF is not
-- using GPS (source set 2): the fix is fake (jammed / spoofed)
local function gps_disagrees()
  if not on_no_gps_src or SLM_GPS_DIFF_M:get() <= 0 then return false end
  local ekf = ahrs:get_location()
  local fix = gps:location(gps:primary_sensor())
  if ekf == nil or fix == nil then return false end
  return ekf:get_distance(fix) > SLM_GPS_DIFF_M:get()
end

local function gps_good()
  local inst = gps:primary_sensor()
  if gps:status(inst) < GPS_OK_FIX_3D then return false end
  if gps:num_sats(inst) < SLM_MIN_SATS:get() then return false end
  return gps:get_hdop(inst) <= SLM_MAX_HDOP:get() * 100   -- hdop is x100
end

-- true if the mission is already inside a landing sequence (current nav
-- item comes after a DO_LAND_START), or the vehicle reports it is landing
local function in_landing_sequence()
  if vehicle:is_landing() then return true end
  local cur = mission:get_current_nav_index()
  for i = 1, cur - 1 do
    local item = mission:get_item(i)
    if item and item:command() == CMD_DO_LAND_START then return true end
  end
  return false
end

local function end_episode()
  use_no_gps_sources(false)
  in_episode = false
  bad_ms = 0
  good_since_ms = nil
  acted = false
end

-- switch to RTL once SLM_GPS_LOSS_S of bad GPS has accumulated in this episode
local function maybe_act()
  if acted or bad_ms < SLM_GPS_LOSS_S:get() * 1000 then return end
  if vehicle:get_mode() == MODE_AUTO and vehicle:is_taking_off() then
    return    -- let the takeoff climb finish; act on a later update
  end
  if vehicle:get_mode() == MODE_AUTO and in_landing_sequence() then
    gcs:send_text(MAV_SEVERITY.WARNING, "SILM: GPS bad during landing - landing continues")
  elseif vehicle:get_mode() == MODE_AUTO then
    if vehicle:set_mode(MODE_RTL) then
      gcs:send_text(MAV_SEVERITY.CRITICAL,
        string.format("SILM: GPS bad %.0fs -> RTL/landing on dead reckoning", bad_ms / 1000))
    else
      gcs:send_text(MAV_SEVERITY.ERROR, "SILM: RTL mode change refused")
    end
  end
  -- Mark handled whatever happened, so a mode set from a ground station is
  -- never overridden and nothing re-triggers in this episode.
  acted = true
end

-- landed without GPS: disarm once certainly on the ground (see header)
local ground_since_ms = nil
local function landed_blind_disarm(now)
  local function reset() ground_since_ms = nil end
  local hold = SLM_LAND_DIS_S:get()
  if hold <= 0 or not on_no_gps_src or not arming:is_armed()
      or vehicle:get_mode() ~= MODE_AUTO or mission:get_current_nav_id() ~= CMD_NAV_LAND then
    return reset()
  end
  local stall = ((AIRSPEED_MIN and AIRSPEED_MIN:get()) or 12) / 1.3
  local aspd = ahrs:airspeed_EAS()
  local gyro = ahrs:get_gyro()
  local down = ahrs:get_relative_position_D_home()
  if aspd == nil or aspd >= 0.8 * stall or gyro == nil or gyro:length() > 0.1
      or down == nil or -down > 5.0 then
    return reset()
  end
  if ground_since_ms == nil then ground_since_ms = now end
  if (now - ground_since_ms):tofloat() >= hold * 1000 then
    gcs:send_text(MAV_SEVERITY.WARNING, "SILM: landed without GPS - disarming")
    arming:disarm()
    reset()
  end
end

local function update()
  update_arming_gate(gps_good())
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
  landed_blind_disarm(now)
  local flying = arming:is_armed() and vehicle:get_likely_flying()

  if not in_episode then
    if good or not flying then
      return update, RUN_MS
    end
    in_episode = true
    bad_ms = 0
    good_since_ms = nil
    gcs:send_text(MAV_SEVERITY.WARNING, "SILM: GPS lost/degraded, dead reckoning")
    use_no_gps_sources(true)
  end

  -- inside an episode
  if good then
    if good_since_ms == nil then good_since_ms = now end
    if (now - good_since_ms):tofloat() >= SLM_GPS_OK_S:get() * 1000 then
      gcs:send_text(MAV_SEVERITY.NOTICE, "SILM: GPS recovered")
      end_episode()
      return update, RUN_MS
    end
    -- a "good-looking" fix far from the GPS-free EKF position is fake:
    -- it counts towards the RTL decision (but not against recovery)
    if flying and gps_disagrees() then
      bad_ms = bad_ms + dt_ms
      maybe_act()
    end
    return update, RUN_MS
  end

  good_since_ms = nil
  if not flying then
    -- On the ground with the GPS still bad: KEEP the EKF off GPS. Switching
    -- back here let a still-jammed receiver's fake movement convince the
    -- landing detector the aircraft was flying again, so it stayed armed on
    -- the ground for minutes instead of disarming (SITL). The episode ends
    -- the normal way: GPS good for SLM_GPS_OK_S.
    return update, RUN_MS
  end
  bad_ms = bad_ms + dt_ms
  maybe_act()
  return update, RUN_MS
end

gcs:send_text(MAV_SEVERITY.INFO, "SILM GPS-loss failsafe loaded")
return update, 1000
