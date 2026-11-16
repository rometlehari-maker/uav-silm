"""Start the UAV SILM Ground Station desktop app (the packaged program's entry).

`--ardupilot-uploader ...` runs ArduPilot's firmware uploader instead: the app
starts itself that way to update the flight controller in its own process.
"""

import os
# pymavlink's default dialect "all" is generated from XML at run time, which the
# packaged app does not carry; the ArduPilot dialect is bundled.
os.environ.setdefault("MAVLINK_DIALECT", "ardupilotmega")
os.environ.setdefault("MAVLINK20", "1")
import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--ardupilot-uploader":
        from groundstation.vendor import ardupilot_uploader
        sys.argv = ["uploader"] + sys.argv[2:]
        ardupilot_uploader.main()
        sys.exit(0)
    from groundstation.desktop import main
    sys.exit(main())
