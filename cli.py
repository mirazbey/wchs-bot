import argparse
import sys
import json

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from iga_client import IGAClient


def main():
    parser = argparse.ArgumentParser(
        description="IGA Istanbul Airport (IST) Live Gate & Flight Lookup Tool"
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Flight number (e.g. TK2170, TK0354) or city name to search"
    )
    parser.add_argument(
        "-g", "--gate-only",
        action="store_true",
        help="Print only the gate number (e.g. 'G5A')"
    )
    parser.add_argument(
        "-s", "--search",
        type=str,
        default=None,
        help="Search flights by city, airline or flight code"
    )
    parser.add_argument(
        "-a", "--arrivals",
        action="store_true",
        help="Query arrivals instead of departures"
    )
    parser.add_argument(
        "-j", "--json",
        action="store_true",
        help="Output raw JSON data"
    )
    parser.add_argument(
        "-l", "--limit",
        type=int,
        default=15,
        help="Limit number of flight search results (default: 15)"
    )

    args = parser.parse_args()
    client = IGAClient()

    target = args.search or args.query
    if not target:
        nature = 0 if args.arrivals else 1
        label = "GELEN (ARRIVALS)" if args.arrivals else "GIDEN (DEPARTURES)"
        print(f"=== IGA ISTANBUL HAVALIMANI CANLI {label} TABLOSU ===")
        flights = client.fetch_flights(nature=nature, page_size=args.limit)
        if args.json:
            print(json.dumps([f.to_dict() for f in flights], indent=2, ensure_ascii=False))
            return

        print(f"{'UCUS NO':<10} | {'HAVAYOLU':<20} | {'HEDEF/KALKIS':<18} | {'SAAT':<16} | {'KAPI':<6} | {'KONTUAR':<8} | {'DURUM'}")
        print("-" * 105)
        for f in flights:
            time_str = f.scheduled_datetime.replace('T', ' ')[:16] if f.scheduled_datetime else '-'
            city = f.to_city if f.nature == 'DEPARTURE' else f.from_city
            gate = f.gate or '-'
            counter = f.counter or '-'
            status = f.status or '-'
            print(f"{f.flight_number:<10} | {f.airline_name[:20]:<20} | {city[:18]:<18} | {time_str:<16} | {gate:<6} | {counter:<8} | {status}")
        return

    # If gate-only or specific flight lookup
    if not args.search and args.query:
        flight = client.get_flight_gate(args.query)
        if not flight:
            if args.gate_only:
                print("N/A")
            else:
                print(f"[!] '{args.query}' numarali ucus IGA sisteminde bulunamadi.")
            sys.exit(1)

        if args.gate_only:
            print(flight.gate or "BELIRLENMEDI")
            return

        if args.json:
            print(json.dumps(flight.to_dict(), indent=2, ensure_ascii=False))
            return

        print("=" * 60)
        print(f"  IGA ISTANBUL AIRPORT - UCUS & KAPI BILGISI")
        print("=" * 60)
        print(f"  Ucus Numarasi : {flight.flight_number}")
        if flight.codeshare:
            print(f"  Ortak Ucuslar : {', '.join(flight.codeshare)}")
        print(f"  Havayolu      : {flight.airline_name} ({flight.airline_code})")
        print(f"  Guzergah      : {flight.from_city} -> {flight.to_city}")
        print(f"  Planlanan Saat: {flight.scheduled_datetime or '-'}")
        print(f"  Tahmini Saat  : {flight.estimated_datetime or '-'}")
        print(f"  --------------------------------------------------")
        print(f"  >>> KAPI (GATE)       : {flight.gate if flight.gate else 'Henuz Belirlenmedi'}")
        print(f"  >>> CHECK-IN KONTUAR  : {flight.counter if flight.counter else '-'}")
        print(f"  >>> DURUM             : {flight.status if flight.status else '-'}")
        print("=" * 60)
        return

    # Search mode
    nature = 0 if args.arrivals else 1
    flights = client.search(target, nature=nature, limit=args.limit)
    if args.json:
        print(json.dumps([f.to_dict() for f in flights], indent=2, ensure_ascii=False))
        return

    print(f"'{target}' aramasi icin bulunan ucuslar ({len(flights)} sonuc):")
    print(f"{'UCUS NO':<10} | {'HAVAYOLU':<20} | {'HEDEF/KALKIS':<18} | {'SAAT':<16} | {'KAPI':<6} | {'KONTUAR':<8} | {'DURUM'}")
    print("-" * 105)
    for f in flights:
        time_str = f.scheduled_datetime.replace('T', ' ')[:16] if f.scheduled_datetime else '-'
        city = f.to_city if f.nature == 'DEPARTURE' else f.from_city
        gate = f.gate or '-'
        counter = f.counter or '-'
        status = f.status or '-'
        print(f"{f.flight_number:<10} | {f.airline_name[:20]:<20} | {city[:18]:<18} | {time_str:<16} | {gate:<6} | {counter:<8} | {status}")


if __name__ == "__main__":
    main()
