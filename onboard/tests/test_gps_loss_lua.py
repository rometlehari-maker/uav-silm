"""Behavioural tests for ardupilot/scripts/silm_gps_loss.lua.

The real script is loaded into a Lua interpreter (lupa) with mocked ArduPilot
bindings (millis, gps, vehicle, arming, param, gcs). Each test drives a
scenario by advancing simulated time and changing GPS / mode / arm state, then
checks what the script did. Requires: pip install lupa
"""

import os

import pytest

lupa = pytest.importorskip("lupa")

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "ardupilot", "scripts", "silm_gps_loss.lua")

MOCKS = r"""
sim = {now=0, gps_status=3, sats=14, hdop=80, armed=true, flying=true, mode=10, msgs={}, mode_changes={},
       src_set=0, src_changes={}, home_set=true, origin=true, auth=nil, auth_calls=0,
       gps_off_m=0, aspd=17.0, gyro=0.3, down=-50.0, nav_id=16, disarms=0,
       taking_off=false, landing=false, nav_index=3, mission={16, 22, 16, 16, 16, 189, 16, 21}}
params = {AIRSPEED_MIN = 13}

local U32 = {}
U32.__index = U32
U32.__sub = function(a, b) return setmetatable({v=a.v-b.v}, U32) end
function U32:tofloat() return self.v end
function millis() return setmetatable({v=sim.now}, U32) end

gps = {}
function gps:primary_sensor() return 0 end
function gps:status(i) return sim.gps_status end
function gps:num_sats(i) return sim.sats end
function gps:get_hdop(i) return sim.hdop end
local Loc = {}
Loc.__index = Loc
function Loc:get_distance(o) return math.abs(self.x - o.x) end
function gps:location(i) return setmetatable({x=sim.gps_off_m}, Loc) end

arming = {}
function arming:is_armed() return sim.armed end
function arming:get_aux_auth_id() return 0 end
function arming:disarm() sim.armed = false; sim.disarms = sim.disarms + 1; return true end
function arming:set_aux_auth_passed(id) sim.auth = "passed"; sim.auth_calls = sim.auth_calls + 1 end
function arming:set_aux_auth_failed(id, msg) sim.auth = msg; sim.auth_calls = sim.auth_calls + 1 end

vehicle = {}
function vehicle:get_likely_flying() return sim.flying end
function vehicle:get_mode() return sim.mode end
function vehicle:set_mode(m) sim.mode = m; table.insert(sim.mode_changes, m); return true end
function vehicle:is_taking_off() return sim.taking_off end
function vehicle:is_landing() return sim.landing end

mission = {}
function mission:get_current_nav_index() return sim.nav_index end
function mission:get_current_nav_id() return sim.nav_id end
function mission:get_item(i)
  local cmd = sim.mission[i + 1]          -- index 0 = home
  if cmd == nil then return nil end
  return {command = function(self) return cmd end}
end

ahrs = {}
function ahrs:home_is_set() return sim.home_set end
function ahrs:airspeed_EAS() return sim.aspd end
function ahrs:get_gyro() return {length = function(self) return sim.gyro end} end
function ahrs:get_relative_position_D_home() return sim.down end
function ahrs:get_location() return setmetatable({x=0}, Loc) end
function ahrs:get_origin() if sim.origin then return {} end return nil end
function ahrs:set_posvelyaw_source_set(i) sim.src_set = i; table.insert(sim.src_changes, i) end

gcs = {}
function gcs:send_text(sev, txt) table.insert(sim.msgs, txt) end

param = {}
local tables = {}
function param:add_table(key, prefix, n) tables[key] = prefix; return true end
function param:add_param(key, idx, name, default)
  params[tables[key] .. name] = default; return true
end

local P = {}
P.__index = P
function Parameter() return setmetatable({}, P) end
function P:init(name) if params[name] == nil then return false end self.name = name return true end
function P:get() return params[self.name] end
"""


class Sim:
    def __init__(self):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(MOCKS)
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        loader = self.lua.eval("function(src) return load(src, 'silm_gps_loss.lua') end")
        chunk = loader(src)
        assert chunk is not None, "script failed to compile"
        self.fn, self.delay = chunk()
        self.g = self.lua.globals()

    @property
    def s(self):
        return self.g.sim

    def run_for(self, seconds):
        """Advance simulated time, calling the script at the interval it asks for."""
        end = self.s.now + seconds * 1000
        while self.s.now < end:
            self.s.now = self.s.now + self.delay
            self.fn, self.delay = self.fn()

    def mode_changes(self):
        return list(self.s.mode_changes.values())

    def msgs(self):
        return list(self.s.msgs.values())

    def src_changes(self):
        return list(self.s.src_changes.values())


def test_compiles_and_creates_params():
    sim = Sim()
    assert sim.g.params["SLM_ENABLE"] == 1
    assert sim.g.params["SLM_GPS_LOSS_S"] == 5
    assert sim.g.params["SLM_GPS_OK_S"] == 10
    assert sim.g.params["SLM_MIN_SATS"] == 6
    assert sim.g.params["SLM_MAX_HDOP"] == 2.5
    assert sim.g.params["SLM_SRC_SW"] == 1
    assert sim.g.params["SLM_GPS_DIFF_M"] == 50
    assert any("loaded" in m for m in sim.msgs())


def test_auto_gps_loss_triggers_rtl_after_timeout_once():
    sim = Sim()
    sim.run_for(2)
    sim.s.gps_status = 1               # lose GPS
    sim.run_for(4.0)
    assert sim.mode_changes() == []     # not yet (< 5 s)
    sim.run_for(1.5)
    assert sim.mode_changes() == [11]   # RTL
    sim.run_for(20)
    assert sim.mode_changes() == [11]   # exactly once


def test_manual_mode_is_never_overridden():
    sim = Sim()
    sim.s.mode = 5                      # FBWA, pilot flying
    sim.s.gps_status = 0
    sim.run_for(30)
    assert sim.mode_changes() == []


def test_short_dropout_followed_by_real_recovery_does_nothing():
    sim = Sim()
    sim.s.gps_status = 1
    sim.run_for(3)                     # < 5 s bad
    sim.s.gps_status = 3
    sim.run_for(11)                    # good for > SLM_GPS_OK_S: episode over
    assert any("recovered" in m for m in sim.msgs())
    sim.s.gps_status = 1
    sim.run_for(4)                     # new episode, only 4 s bad
    assert sim.mode_changes() == []


def test_brief_recovery_does_not_reset_the_episode():
    """3 s bad, 1 s 'good', 3 s bad: one episode with 6 s bad -> RTL. (A plain
    'continuous loss' timer would have reset and never acted.)"""
    sim = Sim()
    sim.s.gps_status = 1
    sim.run_for(3)
    sim.s.gps_status = 3
    sim.run_for(1)
    sim.s.gps_status = 1
    sim.run_for(3)
    assert sim.mode_changes() == [11]


def test_jamming_with_flickering_lock_triggers_rtl():
    """Like ArduPilot's SIM_GPS1_JAM: lock keeps flickering on and off."""
    sim = Sim()
    for _ in range(15):
        sim.s.gps_status = 1
        sim.run_for(0.6)
        sim.s.gps_status = 3
        sim.run_for(0.4)
    assert sim.mode_changes() == [11]


def test_lock_with_too_few_satellites_counts_as_bad():
    sim = Sim()
    sim.s.sats = 4                     # receiver claims 3D lock on 4 sats
    sim.run_for(6)
    assert sim.mode_changes() == [11]


def test_lock_with_bad_hdop_counts_as_bad():
    sim = Sim()
    sim.s.hdop = 450                   # HDOP 4.5
    sim.run_for(6)
    assert sim.mode_changes() == [11]


def test_healthy_gps_never_triggers():
    sim = Sim()
    sim.run_for(120)
    assert sim.mode_changes() == []


def test_pilot_reselecting_auto_is_respected_in_same_loss_event():
    sim = Sim()
    sim.s.gps_status = 1
    sim.run_for(6)
    assert sim.mode_changes() == [11]
    sim.s.mode = 10                     # pilot puts it back in AUTO
    sim.run_for(30)
    assert sim.mode_changes() == [11]   # script doesn't fight the pilot


def test_new_loss_event_after_recovery_can_trigger_again():
    sim = Sim()
    sim.s.gps_status = 1
    sim.run_for(6)
    sim.s.gps_status = 3
    sim.run_for(11)                    # long enough to end the episode
    sim.s.mode = 10
    sim.s.gps_status = 1
    sim.run_for(6)
    assert sim.mode_changes() == [11, 11]


def test_on_ground_or_disarmed_nothing_happens():
    sim = Sim()
    sim.s.armed = False
    sim.s.flying = False
    sim.s.gps_status = 0
    sim.run_for(30)
    assert sim.mode_changes() == []


def test_disable_param():
    sim = Sim()
    sim.g.params["SLM_ENABLE"] = 0
    sim.s.gps_status = 0
    sim.run_for(30)
    assert sim.mode_changes() == []


def test_custom_timeout_param():
    sim = Sim()
    sim.g.params["SLM_GPS_LOSS_S"] = 10
    sim.s.gps_status = 1
    sim.run_for(8)
    assert sim.mode_changes() == []
    sim.run_for(3)
    assert sim.mode_changes() == [11]


def test_never_interrupts_the_landing_sequence():
    """Mission: 0 home, 1 takeoff, 2-4 survey, 5 DO_LAND_START, 6 approach,
    7 NAV_LAND. GPS lost while flying the approach (item 6): RTL would restart
    the landing from item 5, so the script must leave it alone."""
    sim = Sim()
    sim.s.nav_index = 6
    sim.s.gps_status = 1
    sim.run_for(10)
    assert sim.mode_changes() == []
    assert any("landing continues" in m for m in sim.msgs())


def test_never_interrupts_final_approach_reported_by_vehicle():
    sim = Sim()
    sim.s.landing = True
    sim.s.gps_status = 1
    sim.run_for(10)
    assert sim.mode_changes() == []


def test_waits_for_takeoff_to_finish_then_acts():
    sim = Sim()
    sim.s.taking_off = True
    sim.s.nav_index = 1
    sim.s.gps_status = 1
    sim.run_for(8)
    assert sim.mode_changes() == []          # still climbing out
    sim.s.taking_off = False
    sim.s.nav_index = 2
    sim.run_for(0.5)
    assert sim.mode_changes() == [11]        # acts as soon as the takeoff is done


def test_survey_leg_before_landing_start_still_triggers():
    sim = Sim()
    sim.s.nav_index = 4                      # last survey leg, before DO_LAND_START
    sim.s.gps_status = 1
    sim.run_for(6)
    assert sim.mode_changes() == [11]


# ---- EKF source-set switching (stop fusing a jammed receiver) -------------

def test_ekf_stops_using_gps_at_once_and_resumes_after_recovery():
    sim = Sim()
    sim.run_for(2)
    assert sim.src_changes() == []           # healthy: never touched
    sim.s.gps_status = 1
    sim.run_for(0.5)
    assert sim.s.src_set == 1                 # set 2 (no GPS) from the first bad sample
    sim.s.gps_status = 3
    sim.run_for(5)
    assert sim.s.src_set == 1                 # still in the episode (< SLM_GPS_OK_S)
    sim.run_for(6)
    assert sim.s.src_set == 0                 # GPS good 10 s: back to set 1
    assert sim.src_changes() == [1, 0]


def test_flickering_lock_does_not_toggle_sources():
    sim = Sim()
    for _ in range(15):
        sim.s.gps_status = 1
        sim.run_for(0.6)
        sim.s.gps_status = 3
        sim.run_for(0.4)
    assert sim.src_changes() == [1]           # switched once, stays on no-GPS


def test_gps_stays_ignored_after_landing_until_it_is_really_good():
    """Landed while the GPS is still jammed: the EKF must NOT go back to GPS
    (the fake movement kept the aircraft armed on the ground in SITL). It goes
    back only after SLM_GPS_OK_S of good GPS."""
    sim = Sim()
    sim.s.gps_status = 1
    sim.run_for(6)
    assert sim.mode_changes() == [11] and sim.s.src_set == 1
    sim.s.flying = False
    sim.s.armed = False
    for _ in range(10):                     # jammer flickering on the ground
        sim.s.gps_status = 3
        sim.run_for(2)
        sim.s.gps_status = 1
        sim.run_for(1)
    assert sim.src_changes() == [1]         # never toggled back and forth
    assert sim.mode_changes() == [11]       # no new RTL on the ground
    sim.s.gps_status = 3
    sim.run_for(11)                         # really good now
    assert sim.s.src_set == 0
    assert any("recovered" in m for m in sim.msgs())


def test_new_flight_after_a_landed_episode_starts_clean():
    sim = Sim()
    sim.s.gps_status = 1
    sim.run_for(6)
    sim.s.flying = sim.s.armed = False
    sim.s.gps_status = 3
    sim.run_for(11)                         # recovered on the ground
    sim.s.mode = 10
    sim.s.flying = sim.s.armed = True
    sim.s.gps_status = 1
    sim.run_for(6)
    assert sim.mode_changes() == [11, 11]


def test_source_switch_can_be_disabled():
    sim = Sim()
    sim.g.params["SLM_SRC_SW"] = 0
    sim.s.gps_status = 1
    sim.run_for(10)
    assert sim.src_changes() == []
    assert sim.mode_changes() == [11]         # the RTL failsafe still works


def test_disabling_script_mid_episode_restores_sources():
    sim = Sim()
    sim.s.gps_status = 1
    sim.run_for(2)
    sim.g.params["SLM_ENABLE"] = 0
    sim.run_for(2)
    assert sim.s.src_set == 0


# ---- arming gate (AHRS_OPTIONS=1 removes ArduPilot's own EKF pre-arm check) --

def _on_ground(sim):
    sim.s.armed = False
    sim.s.flying = False


def test_arming_blocked_until_ekf_has_gps_origin():
    sim = Sim()
    _on_ground(sim)
    sim.s.origin = False
    sim.run_for(2)
    assert sim.s.auth == "SILM: waiting for EKF to use GPS"
    sim.s.origin = True
    sim.run_for(1)
    assert sim.s.auth == "passed"


def test_arming_blocked_without_home_or_with_bad_gps():
    sim = Sim()
    _on_ground(sim)
    sim.s.home_set = False
    sim.run_for(1)
    assert sim.s.auth == "SILM: waiting for home"
    sim.s.home_set = True
    sim.s.sats = 4
    sim.run_for(1)
    assert sim.s.auth == "SILM: GPS not good enough"
    sim.s.sats = 14
    sim.run_for(1)
    assert sim.s.auth == "passed"


def test_arming_gate_passes_immediately_when_ready_and_does_not_spam():
    sim = Sim()
    _on_ground(sim)
    sim.run_for(30)
    assert sim.s.auth == "passed"
    assert sim.s.auth_calls == 1


def test_arming_gate_released_when_script_disabled():
    sim = Sim()
    _on_ground(sim)
    sim.s.origin = False
    sim.run_for(1)
    sim.g.params["SLM_ENABLE"] = 0
    sim.run_for(2)
    assert sim.s.auth == "passed"


def test_arming_gate_left_alone_in_flight():
    sim = Sim()                               # armed and flying
    sim.s.origin = False
    sim.run_for(5)
    assert sim.s.auth_calls == 0


# ---- jammed fixes that LOOK good (SITL jamming: RTK fixed, 10+ sats) -------

def _jam(sim, cycles, fake_off_m):
    """Mostly 'perfect' fixes at a fake position, with short dropouts."""
    for _ in range(cycles):
        sim.s.gps_status, sim.s.gps_off_m = 1, 0
        sim.run_for(0.4)
        sim.s.gps_status, sim.s.gps_off_m = 6, fake_off_m
        sim.run_for(1.6)


def test_fake_good_fixes_far_from_ekf_count_as_bad():
    sim = Sim()
    _jam(sim, 3, fake_off_m=800)              # 6 s: only 1.2 s of real dropouts
    assert sim.mode_changes() == [11]


def test_fake_good_fixes_near_ekf_count_as_good():
    """Without the position check only the dropouts count (old behaviour)."""
    sim = Sim()
    _jam(sim, 3, fake_off_m=10)
    assert sim.mode_changes() == []


def test_position_check_can_be_disabled():
    sim = Sim()
    sim.g.params["SLM_GPS_DIFF_M"] = 0
    _jam(sim, 3, fake_off_m=800)
    assert sim.mode_changes() == []


def test_position_check_needs_the_no_gps_ekf():
    """With SLM_SRC_SW=0 the EKF follows the GPS, so a disagreement means
    nothing and must not be used."""
    sim = Sim()
    sim.g.params["SLM_SRC_SW"] = 0
    _jam(sim, 3, fake_off_m=800)
    assert sim.mode_changes() == []


def test_far_gps_outside_an_episode_does_nothing():
    sim = Sim()
    sim.s.gps_off_m = 800                     # quality fine, no episode
    sim.run_for(30)
    assert sim.mode_changes() == [] and sim.src_changes() == []


def test_genuine_gps_far_from_drifted_estimate_still_recovers():
    """After a long blind leg the real GPS can be 150 m from the estimate:
    10 s of good quality must still end the episode."""
    sim = Sim()
    sim.s.gps_status = 1
    sim.run_for(30)
    assert sim.mode_changes() == [11]
    sim.s.gps_status, sim.s.gps_off_m = 3, 150
    sim.run_for(11)
    assert any("recovered" in m for m in sim.msgs())
    assert sim.s.src_set == 0


# ---- landed without GPS: disarm (ArduPlane alone stays armed, SITL) -------

def _landed_blind(sim):
    """GPS-loss episode, then on the ground on the LAND item, stopped."""
    sim.s.gps_status = 1
    sim.run_for(6)                          # episode + RTL
    sim.s.mode, sim.s.nav_id = 10, 21       # AUTO, landing sequence, LAND item
    sim.s.aspd, sim.s.gyro, sim.s.down = 2.0, 0.02, -0.4
    sim.s.flying = False                    # ArduPlane's own guess flickers


def test_landed_without_gps_disarms_after_15_s():
    sim = Sim()
    assert sim.g.params["SLM_LAND_DIS_S"] == 15
    _landed_blind(sim)
    sim.run_for(14)
    assert sim.s.armed and sim.s.disarms == 0
    sim.run_for(2)
    assert not sim.s.armed and sim.s.disarms == 1
    assert any("landed without GPS - disarming" in m for m in sim.msgs())


@pytest.mark.parametrize("change", [
    {"aspd": 12.0},          # still flying faster than 80 % of stall (10 m/s)
    {"gyro": 0.5},           # still rolling / tumbling
    {"down": -12.0},         # 12 m up
    {"nav_id": 16},          # on the approach waypoint, not the LAND item
    {"mode": 11},            # not in AUTO
])
def test_never_disarms_unless_certainly_on_the_ground(change):
    sim = Sim()
    _landed_blind(sim)
    for k, v in change.items():
        setattr(sim.s, k, v)
    sim.run_for(60)
    assert sim.s.armed and sim.s.disarms == 0


def test_a_moment_of_movement_restarts_the_15_s():
    sim = Sim()
    _landed_blind(sim)
    sim.run_for(10)
    sim.s.gyro = 0.4                         # bumped / blown over
    sim.run_for(1)
    sim.s.gyro = 0.02
    sim.run_for(13)
    assert sim.s.armed
    sim.run_for(3)
    assert not sim.s.armed


def test_with_good_gps_arduplane_disarms_itself():
    sim = Sim()                              # no episode: GPS fine
    sim.s.mode, sim.s.nav_id = 10, 21
    sim.s.aspd, sim.s.gyro, sim.s.down = 2.0, 0.02, -0.4
    sim.run_for(60)
    assert sim.s.disarms == 0


def test_landed_disarm_can_be_switched_off():
    sim = Sim()
    sim.g.params["SLM_LAND_DIS_S"] = 0
    _landed_blind(sim)
    sim.run_for(60)
    assert sim.s.armed and sim.s.disarms == 0
