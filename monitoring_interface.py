"""Compatibility launcher for EMERS versions before 0.1."""

import argparse

from emers.monitor import run


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the EMERS monitoring interface.")
    parser.add_argument("--ip", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    run(host=args.ip, port=args.port, debug=args.debug)
