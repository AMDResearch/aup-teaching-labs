#!/usr/bin/env python
# Copyright (C) 2025 Advanced Micro Devices, Inc. All rights reserved.
"""Work out which /dev/ttyACM* is which arm, by counting the motors on each bus.

    python common/find_ports.py              # report
    eval "$(python common/find_ports.py --export)"   # sets PORT1 and PORT2

The scripts default to /dev/ttyACM0 and /dev/ttyACM1. Those numbers are not a property of the
device: cdc_acm hands out the lowest free minor at enumeration time, so an adapter that drops off
the bus and comes back can take the other arm's number. Measured on this machine, 3-1.2 was
ttyACM0 at 11:27 and ttyACM1 at 11:30.

Swapped ports do not raise anything. lerobot's handshake only checks that the ids it wants are
present, and a 9-motor bus satisfies a request for 8 -- so every joint command lands on the wrong
motor, quietly. This tells them apart by motor count instead: the left bus carries the left arm
and the head (8 motors, ids 1-8), the right bus carries the right arm and the three wheels (9,
ids 1-9).

Run it after re-plugging, or whenever the arms behave as though they were exchanged.
"""

import argparse
import glob
import sys

from lerobot.motors.feetech import FeetechMotorsBus

BAUDRATE = 1_000_000          # what XLerobot's buses run at
EXPECTED = {8: "PORT1", 9: "PORT2"}
LABEL = {8: "left arm + head", 9: "right arm + wheels"}


def count_motors(port):
    """Return the number of motors answering on *port*, or None if it cannot be opened."""
    bus = FeetechMotorsBus(port, {})
    try:
        bus._connect(handshake=False)
        bus.set_baudrate(BAUDRATE)
        found = bus.broadcast_ping() or {}
        return len(found)
    except Exception:
        return None
    finally:
        try:
            bus.port_handler.closePort()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", action="store_true", help="print shell assignments for eval")
    args = ap.parse_args()

    ports = sorted(glob.glob("/dev/ttyACM*"))
    if not ports:
        sys.exit("no /dev/ttyACM* found -- are the adapters plugged in?")

    found = {}
    for p in ports:
        n = count_motors(p)
        if not args.export:
            what = LABEL.get(n, "unrecognised" if n else "no response")
            print(f"  {p:16} {n if n is not None else '-':>3} motors   {what}")
        if n in EXPECTED:
            if n in found:
                sys.exit(f"two buses report {n} motors ({found[n]} and {p}) -- cannot tell them apart")
            found[n] = p

    missing = [f"{LABEL[k]} ({k} motors)" for k in EXPECTED if k not in found]
    if missing:
        sys.exit("could not find: " + ", ".join(missing))

    if args.export:
        print(f"PORT1={found[8]}; PORT2={found[9]}; export PORT1 PORT2")
    else:
        print(f"\n  PORT1={found[8]}   # {LABEL[8]}\n  PORT2={found[9]}   # {LABEL[9]}")
        print("\nto use them:  eval \"$(python common/find_ports.py --export)\"")


if __name__ == "__main__":
    main()
