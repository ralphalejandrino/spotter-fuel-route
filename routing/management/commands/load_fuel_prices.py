"""Load the supplied fuel-price CSV into the station table.

Everything expensive happens here, once, offline -- so that a request never geocodes,
never aggregates and never cleans. See README "Why the data pipeline is a build step".

What this command has to cope with in the supplied file (all measured, not assumed):

  * 8,151 data rows but only 6,738 distinct OPIS Truckstop IDs. 678 IDs repeat; 597 of
    those repeats disagree on price and none disagree on city/state or Rack ID. They are
    repeated price observations, so they are aggregated (median by default -- robust to
    the observed max spread of $0.90 within a single station).
  * 620 rows are Canadian (AB, BC, MB, NB, NS, ON, QC, SK, YT). The brief says both
    endpoints are within the USA, so these are excluded and counted.
  * 96.6% of `Address` values are highway-relative ("I-44, EXIT 283 & US-69"), not street
    addresses. Street-level geocoding is therefore impossible; city centroids are used.
  * 1,259 rows carry untrimmed whitespace in City or Truckstop Name.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.models import FuelStation

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from build_gazetteer import lookup  # noqa: E402

DATA = Path(__file__).resolve().parents[3] / "data"

EXPECTED_COLUMNS = [
    "OPIS Truckstop ID",
    "Truckstop Name",
    "Address",
    "City",
    "State",
    "Rack ID",
    "Retail Price",
]

CANADA = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}


class Command(BaseCommand):
    help = "Load fuel stations from the assessment CSV, geocoding them offline."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            nargs="?",
            default=str(DATA / "fuel-prices-for-be-assessment.csv"),
        )
        parser.add_argument(
            "--aggregate",
            choices=["median", "mean", "min", "max"],
            default="median",
            help="How to collapse repeated price observations for one station.",
        )

    def handle(self, *args, **opts):
        path = Path(opts["csv_path"])
        if not path.exists():
            raise CommandError(f"CSV not found: {path}")

        gaz_path, gap_path = DATA / "us_gazetteer.json", DATA / "gap_geocodes.json"
        if not gaz_path.exists():
            raise CommandError(
                f"{gaz_path} missing -- run `python scripts/build_gazetteer.py` first."
            )
        gaz = {
            tuple(k.split("|")): tuple(v)
            for k, v in json.loads(gaz_path.read_text()).items()
        }
        gaps = json.loads(gap_path.read_text()) if gap_path.exists() else {}

        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            header = [h.strip() for h in (reader.fieldnames or [])]
            # Fail loud: a header change would otherwise yield an empty station table,
            # and an empty table produces a confident, cheap, completely wrong answer.
            if header != EXPECTED_COLUMNS:
                raise CommandError(
                    "Unexpected CSV schema.\n"
                    f"  expected: {EXPECTED_COLUMNS}\n"
                    f"  actual:   {header}"
                )
            rows = list(reader)

        groups: dict[int, list[dict]] = defaultdict(list)
        skipped_non_us = 0
        for r in rows:
            state = r["State"].strip().upper()
            if state in CANADA:
                skipped_non_us += 1
                continue
            groups[int(r["OPIS Truckstop ID"])].append(r)

        agg = {
            "median": statistics.median,
            "mean": statistics.mean,
            "min": min,
            "max": max,
        }[opts["aggregate"]]

        stations, ungeocoded = [], []
        for opis_id, observations in groups.items():
            first = observations[0]
            city = " ".join(first["City"].split())
            state = first["State"].strip().upper()

            coord = lookup(gaz, city, state) or gaps.get(f"{city.upper()}|{state}")
            source = "gazetteer" if lookup(gaz, city, state) else "nominatim"
            if not coord:
                # Never guess a location. A station placed in the wrong state could be
                # chosen as an "optimal" stop that does not exist on the route -- far
                # worse than one honestly omitted.
                ungeocoded.append(f"{city}, {state}")
                continue

            stations.append(
                FuelStation(
                    opis_id=opis_id,
                    name=" ".join(first["Truckstop Name"].split()),
                    address=" ".join(first["Address"].split()),
                    city=city,
                    state=state,
                    retail_price=round(
                        agg([float(o["Retail Price"]) for o in observations]), 5
                    ),
                    lat=coord[0],
                    lon=coord[1],
                    price_observations=len(observations),
                    geocode_source=source,
                )
            )

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)

        w, s = self.style.WARNING, self.style.SUCCESS
        self.stdout.write(f"CSV rows read              {len(rows):>7,}")
        self.stdout.write(f"  excluded, non-US (CA)    {skipped_non_us:>7,}")
        self.stdout.write(f"  distinct US stations     {len(groups):>7,}")
        self.stdout.write(
            f"  aggregated by            {opts['aggregate']:>7}  "
            f"({sum(1 for g in groups.values() if len(g) > 1):,} had repeat observations)"
        )
        if ungeocoded:
            self.stdout.write(
                w(f"  EXCLUDED, no geocode     {len(ungeocoded):>7,}  "
                  f"e.g. {', '.join(sorted(set(ungeocoded))[:3])}")
            )
        pct = 100 * len(stations) / max(len(groups), 1)
        self.stdout.write(s(f"LOADED                     {len(stations):>7,}  ({pct:.1f}% of US stations)"))
