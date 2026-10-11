"""Service entry point and operator commands."""

import argparse
import getpass
import math
import sys


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description="Submerge service and diagnostics")
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser(
        "doctor", help="check configuration without fetching subscriptions"
    )
    doctor.add_argument(
        "--require-routing", choices=("happ", "v2raytun"), action="append", default=[]
    )
    verify = commands.add_parser("verify", help="check a public subscription after deployment")
    verify.add_argument(
        "--url-stdin", action="store_true", help="read the URL from stdin, without echo"
    )
    verify.add_argument("--client", choices=("happ", "v2raytun", "generic"), default="happ")
    verify.add_argument("--require-routing", action="store_true")
    verify.add_argument("--min-links", type=positive_int, default=1)
    verify.add_argument("--timeout", type=positive_float, default=30)
    args = parser.parse_args(argv)
    if args.command is None:
        from .server import main as serve

        serve()
        return 0
    from .diagnostics import check_public_subscription, doctor_report

    if args.command == "doctor":
        return doctor_report(args.require_routing)
    if args.require_routing and args.client == "generic":
        parser.error("--require-routing needs --client happ or v2raytun")
    try:
        if args.url_stdin:
            url = sys.stdin.readline().strip()
        elif sys.stdin.isatty():
            url = getpass.getpass("Subscription URL (hidden): ").strip()
        else:
            print("FAIL: use --url-stdin when no terminal is available")
            return 1
    except (EOFError, KeyboardInterrupt):
        print("FAIL: no subscription URL supplied")
        return 1
    return check_public_subscription(
        url, args.client, args.require_routing, args.min_links, args.timeout
    )
