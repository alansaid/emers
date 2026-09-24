import argparse

from emers.cli import main


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run continuous measurement.')
    parser.add_argument(
        '--source_name', '--device_name', dest='source_name', type=str, required=True
    )
    parser.add_argument('--polling_rate', type=float, required=False, default=0.5)
    parser.add_argument('--log_interval', type=int, required=False, default=300)
    args = parser.parse_args()

    main([
        "measure",
        "--source", args.source_name,
        "--polling-rate", str(args.polling_rate),
        "--log-interval", str(args.log_interval),
    ])
