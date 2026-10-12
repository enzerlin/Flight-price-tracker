import os
import subprocess
import sys
from dotenv import load_dotenv


load_dotenv()
ORIGIN = os.getenv("FLIGHT_ORIGIN")
# Supports a comma-separated list while keeping FLIGHT_DESTINATION compatible
# with existing one-route configurations.
DESTINATIONS = [
    destination.strip().upper()
    for destination in os.getenv(
        "FLIGHT_DESTINATIONS", os.getenv("FLIGHT_DESTINATION", "")
    ).split(",")
    if destination.strip()
]

OUTBOUND_DATE = "2027-01-28"
RETURN_DATE = "2027-02-01"


def run_route(origin, destination):
    env = os.environ.copy()

    env["FLIGHT_ORIGIN"] = origin
    env["FLIGHT_DESTINATION"] = destination
    env["FLIGHT_DATE"] = OUTBOUND_DATE
    env["FLIGHT_RETURN_DATE"] = RETURN_DATE

    result = subprocess.run(
        [sys.executable, "src/eztravel.py"],
        env=env,
    )

    return result.returncode == 0


def main():

    if not ORIGIN or not DESTINATIONS:
        print("ERROR: FLIGHT_ORIGIN or FLIGHT_DESTINATIONS is not set")
        sys.exit(1)

    failures = []
    for destination in DESTINATIONS:
        print("\n==============================")
        print(f"Running: {ORIGIN} ⇄ {destination}")
        print(f"去程 {OUTBOUND_DATE} | 回程 {RETURN_DATE or '(不搜)'}")
        print("==============================")

        if not run_route(ORIGIN, destination):
            print(f"FAILED: {ORIGIN} ⇄ {destination}")
            failures.append(destination)
        else:
            print(f"SUCCESS: {ORIGIN} ⇄ {destination}")
        print("==============================")

    if failures:
        print("Failed destinations:", ", ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
