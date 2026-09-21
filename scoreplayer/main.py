from __future__ import annotations
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--require-engine", action="store_true")
    parser.add_argument("--omr-smoke", default=None)
    parser.add_argument("--self-test-report", default=None)
    args, _unknown = parser.parse_known_args()

    if args.self_test:
        from scoreplayer.selftest import run_self_test
        raise SystemExit(run_self_test(
            require_engine=args.require_engine,
            omr_smoke_image=args.omr_smoke,
            report_path=args.self_test_report,
        ))

    from scoreplayer.gui import run
    run()


if __name__ == "__main__":
    main()
