"""Service entry point and operator commands."""

import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(description="Submerge service and diagnostics")
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser(
        "doctor", help="check configuration without fetching subscriptions"
    )
    doctor.add_argument(
        "--require-routing", choices=("happ", "v2raytun"), action="append", default=[]
    )
    args = parser.parse_args(argv)
    if args.command is None:
        from .server import main as serve

        serve()
        return 0
    from .diagnostics import doctor_report

    return doctor_report(args.require_routing)
