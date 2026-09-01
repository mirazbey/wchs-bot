import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Union


@dataclass
class FlightInfo:
    id: int
    flight_number: str
    airline_code: str
    airline_name: str
    nature: str  # 'DEPARTURE' or 'ARRIVAL'
    is_international: bool
    from_city: str
    to_city: str
    scheduled_datetime: Optional[str]
    estimated_datetime: Optional[str]
    gate: Optional[str]
    counter: Optional[str]
    carousel: Optional[str]
    status: Optional[str]
    codeshare: List[str]

    @property
    def has_gate(self) -> bool:
        return bool(self.gate and self.gate.strip() and self.gate.strip() != "-")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IGAClient:
    """
    Official IGA Istanbul Airport (IST) Flight and Gate Information Client.
    Directly interfaces with the live Umbraco Flight Information System.
    """

    API_ENDPOINT = "https://www.istairport.com/umbraco/api/FlightInfo/GetFlightStatusBoard"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, culture: str = "tr", timeout: int = 15):
        self.culture = culture
        self.timeout = timeout

    def _post_request(self, payload: Dict[str, str]) -> Dict[str, Any]:
        encoded_data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            self.API_ENDPOINT,
            data=encoded_data,
            headers={
                "User-Agent": self.USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": "https://www.istairport.com/ucuslar/ucus-bilgileri/giden-ucuslar",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw_bytes = resp.read()
                return json.loads(raw_bytes.decode("utf-8", errors="replace"))
        except Exception as e:
            raise RuntimeError(f"Failed to connect to IGA Airport API: {e}") from e

    def _parse_flight(self, item: Dict[str, Any], nature_val: int) -> FlightInfo:
        gate_val = item.get("gate")
        gate_str = str(gate_val).strip() if gate_val and str(gate_val).strip() not in ("", "-", "None") else None
        
        counter_val = item.get("counter")
        counter_str = str(counter_val).strip() if counter_val and str(counter_val).strip() not in ("", "-", "None") else None

        carousel_val = item.get("carousel")
        carousel_str = str(carousel_val).strip() if carousel_val and str(carousel_val).strip() not in ("", "-", "None") else None

        remark_val = item.get("remark")
        remark_str = str(remark_val).strip() if remark_val and str(remark_val).strip() not in ("", "-", "None") else None

        return FlightInfo(
            id=item.get("id", 0),
            flight_number=str(item.get("flightNumber", "")).strip().upper(),
            airline_code=str(item.get("airlineCode", "")).strip().upper(),
            airline_name=str(item.get("airlineName", "")).strip(),
            nature="DEPARTURE" if nature_val == 1 else "ARRIVAL",
            is_international=bool(item.get("isInternational", 0) == 1),
            from_city=str(item.get("fromCityName", "")).strip(),
            to_city=str(item.get("toCityName", "")).strip(),
            scheduled_datetime=item.get("scheduledDatetime"),
            estimated_datetime=item.get("estimatedDatetime"),
            gate=gate_str,
            counter=counter_str,
            carousel=carousel_str,
            status=remark_str,
            codeshare=item.get("codeshare") or []
        )

    def fetch_flights(
        self,
        nature: int = 1,  # 1: Departures, 0: Arrivals
        search_term: str = "",
        is_international: Optional[int] = None,  # None: Both, 0: Domestic, 1: International
        page_size: int = 50,
        date_str: str = "",
        end_date_str: str = ""
    ) -> List[FlightInfo]:
        """
        Fetch flight records matching parameters.
        """
        intl_targets = [0, 1] if is_international is None else [is_international]
        results: List[FlightInfo] = []
        seen_ids = set()

        for intl in intl_targets:
            payload = {
                "nature": str(nature),
                "searchTerm": search_term,
                "pageSize": str(page_size),
                "isInternational": str(intl),
                "date": date_str,
                "endDate": end_date_str,
                "culture": self.culture,
                "clickedButton": ""
            }
            res = self._post_request(payload)
            if res.get("status") and res.get("result", {}).get("data", {}).get("flights"):
                for item in res["result"]["data"]["flights"]:
                    flight_id = item.get("id")
                    if flight_id not in seen_ids:
                        seen_ids.add(flight_id)
                        results.append(self._parse_flight(item, nature))

        return results

    def get_flight_gate(self, flight_number: str) -> Optional[FlightInfo]:
        """
        Directly look up gate information for a flight number (e.g. 'TK2170', 'TK 2170', 'TK0354').
        Checks departures first (domestic and international), and supports codeshare matching.
        """
        clean_num = re.sub(r"\s+", "", flight_number).upper()
        
        # 1. Search directly using search_term
        flights = self.fetch_flights(nature=1, search_term=clean_num, is_international=None, page_size=50)
        
        for f in flights:
            norm_code = re.sub(r"\s+", "", f.flight_number).upper()
            codeshares = [re.sub(r"\s+", "", cs).upper() for cs in f.codeshare]
            if norm_code == clean_num or clean_num in codeshares:
                return f
            
            # Match without leading zeros (e.g., TK354 == TK0354)
            match_no_zero = re.sub(r"([A-Z]+)0+(\d+)", r"\1\2", clean_num)
            f_no_zero = re.sub(r"([A-Z]+)0+(\d+)", r"\1\2", norm_code)
            if match_no_zero == f_no_zero:
                return f

        # 2. Broad scan fallback
        all_recent = self.fetch_flights(nature=1, search_term="", is_international=None, page_size=100)
        for f in all_recent:
            norm_code = re.sub(r"\s+", "", f.flight_number).upper()
            codeshares = [re.sub(r"\s+", "", cs).upper() for cs in f.codeshare]
            if clean_num in norm_code or any(clean_num in cs for cs in codeshares):
                return f

        return None

    def search(self, query: str, nature: int = 1, limit: int = 20) -> List[FlightInfo]:
        """
        Search flights by destination city, flight number, or airline.
        """
        return self.fetch_flights(nature=nature, search_term=query, is_international=None, page_size=limit)
