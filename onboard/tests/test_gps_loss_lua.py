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
sim = {now=0, gps_status=3, sats=14, hdop=80, armed=true, flying=true, mode=10, msgs={}, mode_changes={}}
params = {}

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

arming = {}
function arming:is_armed() return sim.armed end

vehicle = {}
function vehicle:get_likely_flying() return sim.flying end
function vehicle:get_mode() return sim.mode end
function vehicle:set_mode(m) sim.mode = m; table.insert(sim.mode_changes, m); return true end

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


def test_compiles_and_creates_params():
    sim = Sim()
    assert sim.g.params["SLM_ENABLE"] == 1
    assert sim.g.params["SLM_GPS_LOSS_S"] == 5
    assert sim.g.params["SLM_GPS_OK_S"] == 10
    assert sim.g.params["SLM_MIN_SATS"] == 6
    assert sim.g.params["SLM_MAX_HDOP"] == 2.5
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
