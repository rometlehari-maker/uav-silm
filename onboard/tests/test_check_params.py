"""tools/check_params.py against a miniature fake ArduPilot source tree, plus
an optional check of the real param file when ARDUPILOT_SRC is set."""

import os
import subprocess
import sys
import textwrap

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)
import check_params  # noqa: E402

PARAMFILE = os.path.join(os.path.dirname(__file__), "..", "ardupilot", "gps_denied_failsafe.param")


def write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(textwrap.dedent(text))


@pytest.fixture
def fake_src(tmp_path):
    write(tmp_path / "ArduPlane" / "Parameters.cpp", """
        // @Param: FS_LONG_ACTN
        // @Values: 0:Continue,1:RTL
        // @Range: 0 5
        // @Param{Copter}: COPTER_ONLY
        // @Group: FENCE_
        // @Path: ../libraries/AC_Fence/AC_Fence.cpp
        // @Group{Copter}: CPT_
        // @Path: ../libraries/Copter/Thing.cpp
        // @Group: EK3_
        // @Path: ../libraries/EKF/EKF.cpp
    """)
    write(tmp_path / "libraries" / "AC_Fence" / "AC_Fence.cpp", """
        // @Param: MARGIN
        // @Range: 1 10
        // @Param{Plane, Copter}: AUTOENABLE
    """)
    write(tmp_path / "libraries" / "Copter" / "Thing.cpp", """
        // @Param: X
    """)
    write(tmp_path / "libraries" / "EKF" / "EKF.cpp", """
        // @Param: OPTIONS
        // @Group: SRC
        // @Path: Source.cpp
    """)
    write(tmp_path / "libraries" / "EKF" / "Source.cpp", """
        // @Param: 1_POSXY
    """)
    return str(tmp_path)


def test_collect_resolves_groups_nesting_and_vehicle_filters(fake_src):
    k = check_params.collect(fake_src, "Plane")
    assert {"FS_LONG_ACTN", "FENCE_MARGIN", "FENCE_AUTOENABLE", "EK3_OPTIONS",
            "EK3_SRC1_POSXY"} <= set(k)
    assert "COPTER_ONLY" not in k and "CPT_X" not in k
    assert k["FENCE_MARGIN"] == (1.0, 10.0)


def test_cli_flags_unknown_duplicate_and_range(fake_src, tmp_path):
    pf = tmp_path / "t.param"
    pf.write_text("FS_LONG_ACTN,0\nFENCE_MARGIN,30\nFS_DR_ENABLE,1\nFS_LONG_ACTN,1 # dup\n")
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "check_params.py"),
                        "--src", fake_src, str(pf)], capture_output=True, text=True)
    assert r.returncode == 1
    assert "UNKNOWN FS_DR_ENABLE" in r.stdout
    assert "OUT OF RANGE FENCE_MARGIN" in r.stdout
    assert "DUPLICATE FS_LONG_ACTN" in r.stdout


@pytest.mark.skipif(not os.environ.get("ARDUPILOT_SRC"), reason="set ARDUPILOT_SRC to an ArduPilot checkout")
def test_real_param_file_is_valid_for_checked_out_firmware():
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "check_params.py"),
                        "--src", os.environ["ARDUPILOT_SRC"], PARAMFILE],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_param_file_keeps_link_free_mission_settings():
    """Guard the two settings that make link-free AUTO missions work."""
    vals = {}
    for line in open(PARAMFILE):
        line = line.split("#", 1)[0].strip()
        if line:
            name, val = line.split(",")[:2]
            vals[name] = float(val)
    assert vals["FS_LONG_ACTN"] == 0, "1 would abort AUTO missions when out of RC range"
    assert vals["FS_SHORT_ACTN"] == 0
    assert vals["ARSPD_USE"] == 1, "dead reckoning needs airspeed"
    assert vals["SCR_ENABLE"] == 1, "needed for silm_gps_loss.lua"


def test_param_file_flies_without_any_rc_receiver():
    """There is no RC receiver (no ExpressLRS): it must arm, hand-launch, fly,
    land and disarm by itself, and every failsafe must end in a landing."""
    vals = {}
    for line in open(PARAMFILE):
        line = line.split("#", 1)[0].strip()
        if line:
            name, val = line.split(",")[:2]
            vals[name] = float(val)
    assert vals["THR_FAILSAFE"] == 0, "no receiver: an RC failsafe would trigger at once"
    assert int(vals["ARMING_SKIPCHK"]) & 64, "RC arming check must be skipped"
    assert vals["ARMING_MIS_ITEMS"] == 13, "require TAKEOFF + DO_LAND_START + LAND"
    assert vals["FS_GCS_ENABL"] == 0, "no ground station link either"
    assert vals["RTL_AUTOLAND"] == 2, "RTL must fly the landing sequence, not circle"
    assert vals["TKOFF_THR_MINACC"] > 0, "motor must wait for the throw"
    assert vals["TKOFF_THR_DELAY"] >= 2, "pusher prop: keep hands clear"
    assert vals["BATT_FS_LOW_ACT"] == 2 and vals["BATT_FS_CRT_ACT"] == 2, "battery failsafe -> land"
    assert vals["LAND_DISARMDELAY"] > 0, "disarm after touchdown"
    assert vals["FENCE_ACTION"] == 1, "fence breach -> RTL -> landing"
    assert vals["INITIAL_MODE"] == 10, "boot into AUTO: no receiver to select a mode"
