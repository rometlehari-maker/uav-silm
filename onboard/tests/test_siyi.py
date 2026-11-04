"""SIYI gimbal protocol: frames must match the published example frames
byte for byte, and the client must work against a fake gimbal over UDP."""

import socket
import struct
import threading
import time

import pytest

from silm_recon import siyi

# Example frames published in open-source SIYI SDK code (A8miniControl.c).
VECTORS = [
    (0x01, b"", 0, "55 66 01 00 00 00 00 01 64 c4"),       # acquire firmware version
    (0x0D, b"", 0, "55 66 01 00 00 00 00 0d e8 05"),       # acquire attitude
    (0x08, b"\x01", 0, "55 66 01 01 00 00 00 08 01 d1 12"),  # auto centre
    (0x0C, b"\x03", 0, "55 66 01 01 00 00 00 0c 03 57 fe"),  # lock mode
    (0x07, b"\x64\x64", 0, "55 66 01 02 00 00 00 07 64 64 3d cf"),   # rotate 100 100
    (0x0F, b"\x04\x05", 1, "55 66 01 02 00 01 00 0f 04 05 60 bb"),   # absolute zoom, seq 1
]


@pytest.mark.parametrize("cmd,data,seq,hexstr", VECTORS)
def test_frames_match_published_examples(cmd, data, seq, hexstr):
    assert siyi.build_frame(cmd, data, seq) == bytes.fromhex(hexstr)
    assert siyi.parse_frame(bytes.fromhex(hexstr)) == (cmd, data)


def test_parse_rejects_bad_crc_and_garbage():
    good = bytearray(bytes.fromhex("55 66 01 00 00 00 00 0d e8 05"))
    good[-1] ^= 0xFF
    assert siyi.parse_frame(bytes(good)) is None
    assert siyi.parse_frame(b"\x00" * 12) is None
    assert siyi.parse_frame(b"\x55\x66\x01") is None


def test_set_angles_and_attitude_encoding():
    f = siyi.set_angles_frame(0.0, -90.0)
    cmd, data = siyi.parse_frame(f)
    assert cmd == 0x0E and struct.unpack("<hh", data) == (0, -900)
    att = siyi.parse_attitude(struct.pack("<hhhhhh", 15, -895, 32, 0, 0, 0), t=1.0)
    assert (att.yaw, att.pitch, att.roll) == (1.5, -89.5, 3.2)


def test_client_against_fake_gimbal():
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    srv.settimeout(0.2)
    port = srv.getsockname()[1]
    seen = []
    stop = threading.Event()

    def fake():
        pitch = 0
        while not stop.is_set():
            try:
                buf, addr = srv.recvfrom(256)
            except socket.timeout:
                continue
            msg = siyi.parse_frame(buf)
            if not msg:
                continue
            seen.append(msg[0])
            if msg[0] == siyi.CMD_SET_ANGLES:
                pitch = struct.unpack("<hh", msg[1])[1]
            elif msg[0] == siyi.CMD_ACQUIRE_ATTITUDE:
                srv.sendto(siyi.build_frame(siyi.CMD_ACQUIRE_ATTITUDE,
                                            struct.pack("<hhhhhh", 0, pitch, 12, 0, 0, 0),
                                            need_ack=False), addr)

    th = threading.Thread(target=fake, daemon=True)
    th.start()
    g = siyi.SiyiGimbal({"ip": "127.0.0.1", "port": port, "pitch_deg": -90, "poll_hz": 20,
                         "timeout_s": 0.2})
    g.start()
    t0 = time.time()
    while time.time() - t0 < 3 and (g.attitude() is None or g.attitude().pitch != -90.0):
        time.sleep(0.05)
    a = g.attitude()
    g.stop()
    stop.set()
    th.join(1)
    srv.close()
    assert siyi.CMD_SET_ANGLES in seen and siyi.CMD_ACQUIRE_ATTITUDE in seen
    assert a is not None and a.pitch == -90.0 and a.roll == 1.2 and g.answered


def test_client_without_gimbal_reports_nothing():
    g = siyi.SiyiGimbal({"ip": "127.0.0.1", "port": 9, "poll_hz": 20, "timeout_s": 0.05})
    g.start()
    time.sleep(0.3)
    g.stop()
    assert g.attitude() is None and not g.answered
