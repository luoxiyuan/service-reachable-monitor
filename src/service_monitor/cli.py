"""Command line interface.

Sub-commands
------------
``run``        check the selected environments once and alert on failures
``schedule``   run ``run`` repeatedly on a cron schedule (needs APScheduler)
``inventory``  dump Dubbo provider metadata as JSON (no alerting)
``list-envs``  show the configured environments
``doctor``     validate configuration files and report problems

Exit codes: 0 = everything reachable, 1 = at least one failure, 2 = bad usage.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .config import ConfigError, load_monitor_config
from .models import RunReport
from .notifiers.manager import load_notify_config
from .reporting import render_table, setup_logging, to_json, write_json
from .runner import MonitorRunner, build_runner
from .scheduler import parse_cron, start_scheduler

logger = logging.getLogger("service_monitor")

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_USAGE = 2


def _global_options() -> argparse.ArgumentParser:
    """Options accepted both before and after the sub-command.

    Defaults are ``SUPPRESS``ed so that a value given before the sub-command is
    not overwritten by the sub-parser's own default (a long-standing argparse
    gotcha); :func:`_apply_defaults` fills anything still missing.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c", "--config", default=argparse.SUPPRESS,
        help="targets config file (default: targets.yaml, env MONITOR_TARGETS)",
    )
    common.add_argument(
        "-n", "--notify-config", default=argparse.SUPPRESS,
        help="notification config file (default: notify.yaml, env MONITOR_NOTIFY)",
    )
    common.add_argument("--env-file", default=argparse.SUPPRESS, help=".env file with secrets")
    common.add_argument(
        "-e", "--env", action="append", default=argparse.SUPPRESS, metavar="NAME",
        help="environment to check; repeatable (default: all enabled)",
    )
    common.add_argument(
        "-k", "--kind", action="append", default=argparse.SUPPRESS, choices=["http", "dubbo"],
        help="only run these checker kinds; repeatable",
    )
    common.add_argument(
        "--all", dest="include_disabled", action="store_true", default=argparse.SUPPRESS,
        help="include environments marked enabled: false",
    )
    common.add_argument(
        "--no-alert", action="store_true", default=argparse.SUPPRESS,
        help="print results but send no notifications",
    )
    common.add_argument(
        "--json", dest="json_path", default=argparse.SUPPRESS, metavar="PATH",
        help="also write the full report as JSON to PATH",
    )
    common.add_argument(
        "--quiet", action="store_true", default=argparse.SUPPRESS, help="only print failures",
    )
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="debug logging")
    common.add_argument("--log-file", default=argparse.SUPPRESS, help="append logs to this file")
    common.add_argument("--log-level", default=argparse.SUPPRESS,
                        help="log level name (default: INFO, env MONITOR_LOG_LEVEL)")
    return common


#: Values applied when an option was omitted everywhere.
_OPTION_DEFAULTS: Dict[str, Any] = {
    "config": os.environ.get("MONITOR_TARGETS", "targets.yaml"),
    "notify_config": os.environ.get("MONITOR_NOTIFY", "notify.yaml"),
    "env_file": None,
    "env": [],
    "kind": [],
    "include_disabled": False,
    "no_alert": False,
    "json_path": None,
    "quiet": False,
    "verbose": False,
    "log_file": None,
    "log_level": os.environ.get("MONITOR_LOG_LEVEL", "INFO"),
}


def _apply_defaults(args: argparse.Namespace) -> argparse.Namespace:
    for key, value in _OPTION_DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    return args


def build_parser() -> argparse.ArgumentParser:
    common = _global_options()
    parser = argparse.ArgumentParser(
        prog="service-monitor",
        description="Check HTTP and Dubbo service reachability across environments.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", parents=[common], help="check once (default when no sub-command is given)")
    sched = sub.add_parser("schedule", parents=[common],
                           help="check repeatedly on a cron schedule")
    sched.add_argument("--cron", default=os.environ.get("MONITOR_CRON"),
                       help='5-field cron, e.g. "*/10 10,14,16,18 * * mon-fri"')
    inv = sub.add_parser("inventory", parents=[common],
                         help="dump Dubbo provider metadata as JSON")
    inv.add_argument("-o", "--output", default=None, metavar="PATH", help="write JSON here")
    sub.add_parser("list-envs", parents=[common], help="list configured environments")
    sub.add_parser("doctor", parents=[common], help="validate configuration files")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = _apply_defaults(parser.parse_args(argv))
    command = args.command or "run"

    setup_logging("DEBUG" if args.verbose else args.log_level, args.log_file)

    try:
        config = load_monitor_config(args.config, env_file=args.env_file)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if command == "doctor":
        return _doctor(args, config)
    if command == "list-envs":
        return _list_envs(config)

    notify_settings = None
    if not args.no_alert:
        try:
            notify_settings = load_notify_config(args.notify_config, env_file=args.env_file)
        except ConfigError as exc:
            print(f"notify config error: {exc}", file=sys.stderr)
            return EXIT_USAGE

    runner = build_runner(config, notify_settings)

    if command == "inventory":
        return _inventory(runner, args)
    if command == "schedule":
        return _schedule(runner, args)
    return _run_once(runner, args)


# ---------------------------------------------------------------------------
# sub-command implementations
# ---------------------------------------------------------------------------
def _run_once(runner: MonitorRunner, args: argparse.Namespace, quiet: Optional[bool] = None) -> int:
    report = runner.run(
        environments=args.env or None,
        kinds=args.kind or None,
        include_disabled=args.include_disabled,
        alert=not args.no_alert,
    )
    _print_report(report, quiet if quiet is not None else args.quiet)
    if args.json_path:
        path = write_json(report, args.json_path)
        print(f"report written to {path}")
    return EXIT_FAILURES if report.failures else EXIT_OK


def _schedule(runner: MonitorRunner, args: argparse.Namespace) -> int:
    cron = parse_cron(args.cron)

    def job() -> None:
        logger.info("scheduled run starting")
        report = runner.run(
            environments=args.env or None,
            kinds=args.kind or None,
            include_disabled=args.include_disabled,
            alert=not args.no_alert,
        )
        logger.info("scheduled run finished: %s", report.digest())

    job()  # run immediately once, then hand over to the scheduler
    try:
        start_scheduler(job, cron)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def _inventory(runner: MonitorRunner, args: argparse.Namespace) -> int:
    rows = runner.inventory_dubbo(environments=args.env or None)
    text = json.dumps(rows, ensure_ascii=False, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"{len(rows)} provider record(s) written to {out}")
    else:
        print(text)
    return EXIT_OK


def _list_envs(config: Any) -> int:
    if not config.environments:
        print("(no environments configured)")
        return EXIT_OK
    for env in config.environments:
        state = "enabled" if env.enabled else "disabled"
        dubbo = "dubbo+http" if env.dubbo else ("dubbo" if env.dubbo else "http")
        kinds = []
        if env.http_targets:
            kinds.append(f"http={len(env.http_targets)}")
        if env.dubbo:
            kinds.append("dubbo=yes")
        print(f"- {env.name} ({env.display}) [{state}] {' '.join(kinds) or 'empty'}")
    return EXIT_OK


def _doctor(args: argparse.Namespace, config: Any) -> int:
    problems: List[str] = []
    print(f"targets config : {config.source or args.config}")
    print(f"environments   : {len(config.environments)}")
    for env in config.environments:
        if not env.enabled:
            print(f"  - {env.name}: disabled")
            continue
        if not env.http_targets and env.dubbo is None:
            problems.append(f"{env.name}: nothing to check")
        if env.dubbo is not None and not env.dubbo.zk_hosts:
            problems.append(f"{env.name}: dubbo block without zk_hosts")
        print(
            f"  - {env.name}: http={len(env.http_targets)} "
            f"dubbo={'yes' if env.dubbo else 'no'}"
        )

    try:
        notify_settings = load_notify_config(args.notify_config, env_file=args.env_file)
        print(f"notify config  : {notify_settings.source or args.notify_config}")
        print(f"channels       : {', '.join(c.name for c in notify_settings.channels) or '-'}")
        print(f"alarm center   : {'yes' if notify_settings.alarm_center else 'no'}")
        print(f"batch size     : {notify_settings.batch_size}")
    except ConfigError as exc:
        problems.append(f"notify config: {exc}")

    for dep, purpose in (("yaml", "config parsing"), ("requests", "HTTP checks")):
        if _missing(dep):
            problems.append(f"missing required dependency {dep} ({purpose})")
    if _missing("kazoo") and any(e.dubbo for e in config.environments):
        problems.append("missing kazoo: Dubbo checks will fail (pip install kazoo)")

    if problems:
        print("\nPROBLEMS:")
        for item in problems:
            print(f"  * {item}")
        return EXIT_FAILURES
    print("\nconfiguration looks good")
    return EXIT_OK


def _missing(module: str) -> bool:
    try:
        __import__(module)
        return False
    except ImportError:
        return True


def _print_report(report: RunReport, quiet: bool) -> None:
    if quiet:
        for failure in report.failures:
            print(failure.summary_line())
        print(report.digest())
        return
    print(render_table(report.results))
    print()
    print(f"duration: {report.duration_seconds}s | {report.digest()}")
    if report.failures:
        print(f"\n{len(report.failures)} target(s) need attention:")
        for failure in report.failures:
            print(f"  * {failure.summary_line()}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())