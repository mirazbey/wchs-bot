"""
WCHS-IST: İstanbul Havalimanı 7/24 Otonom Transfer & Kapı Radarı
- Sade, hızlı ve operasyonel mesaj formatı (karmaşıklık ve göz yoran kalabalıktan arındırılmış)
- GPT-4o-mini Azure Akıllı Asistanı (doğal sorular ve WhatsApp metinleri için)
- Konum Radarı (ben f3deyim, f4, e2 vb.)
- WhatsApp Köprüsü ve Akıllı İleti Ayrıştırıcı
"""

import json
import html
import threading
from concurrent.futures import ThreadPoolExecutor
from gate_support import GateBridgeClient, UNKNOWN_GATE, exact_gate, gate_targets, normalize_flight
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8629069082:AAHNKyakE_5GuZTdUg5lgyBA1n7JsQez-1o")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "968928191")

OPENAI_ENDPOINT = os.environ.get("OPENAI_ENDPOINT", "https://swedencentral.api.cognitive.microsoft.com/openai/deployments/gpt-4o-mini/chat/completions?api-version=2024-08-01-preview")
OPENAI_KEY = os.environ.get("OPENAI_KEY", "")
WA_BRIDGE_URL = os.environ.get("WA_BRIDGE_URL", "http://localhost:5005")

OSS_AIRPORTS = {
    # Almanya
    "FRA", "MUC", "BER", "DUS", "HAM", "STR", "CGN", "HAJ", "NUE", "LEJ", "BRE", "FMO", "PAD",
    # Fransa
    "CDG", "ORY", "LYS", "NCE", "MRS", "TLS", "BOD", "SXB", "NTE",
    # Birlesik Krallik & Irlanda
    "LHR", "LGW", "MAN", "BHX", "EDI", "STN", "LTN", "BRS", "NCL", "DUB",
    # Isvicre & Avusturya
    "ZRH", "GVA", "BSL", "VIE", "SZG", "INN",
    # Italya
    "FCO", "MXP", "BLQ", "VCE", "NAP", "CTA", "PMO", "BRI", "PSA", "VRN", "TRN",
    # Ispanya & Portekiz
    "MAD", "BCN", "AGP", "VLC", "BIO", "LIS", "OPO",
    # Beneluks
    "AMS", "BRU", "LUX",
    # Iskandinavya
    "CPH", "BLL", "ARN", "GOT", "OSL", "BGO", "HEL", "KEF",
    # Orta & Dogu Avrupa (AB / OSS)
    "PRG", "WAW", "KRK", "BUD", "ATH", "SKG", "HER", "RHO", "OTP", "CLJ", "SOF", "VAR", "ZAG", "DBV", "SPU", "LJU", "RIX", "VNO", "TLL", "MLA",
    # ABD
    "JFK", "EWR", "ORD", "LAX", "MIA", "SFO", "BOS", "IAD", "IAH", "DFW", "ATL", "SEA", "DTW", "PHL", "DEN", "MCO",
    # Kanada
    "YYZ", "YUL", "YVR"
}

CONFIG = {
    "filter_mode": "ALL",
    "selected_pier": None,
    "only_international": True,
    "user_gate": "F3"
}

last_update_id = 0
cached_flights = {"time": 0, "arrivals": [], "departures": [], "source": ""}
custom_flight_gates = {}
custom_gate_times = {}
crawler_stats = {
    "total_queries": 0,
    "confirmed_gates": 0,
    "last_flight": None,
    "last_status": None,
    "started_at": time.time()
}
bridge_client = GateBridgeClient(WA_BRIDGE_URL)
telegram_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="TelegramQueries")
telegram_slots = threading.BoundedSemaphore(6)


confirmed_arrival_store = {}
GATE_CACHE_TTL = 3600  # 60 dakika hafızada tut


def set_manual_gate(flight, gate, origin_name="", arr_time=None):
    clean = normalize_flight(flight)
    g = exact_gate(gate)
    if not g:
        return
    now_m = time.monotonic()
    custom_flight_gates[clean] = g
    custom_gate_times[clean] = now_m
    confirmed_arrival_store[clean] = {
        "flight_no": clean,
        "origin_name": origin_name,
        "arr_time": arr_time or get_now_ist(),
        "gate": g,
        "time": now_m
    }


def manual_gate(flight):
    clean = normalize_flight(flight)
    if time.monotonic() - custom_gate_times.get(clean, -1e9) < GATE_CACHE_TTL:
        return custom_flight_gates.get(clean)
    custom_flight_gates.pop(clean, None)
    custom_gate_times.pop(clean, None)
    confirmed_arrival_store.pop(clean, None)
    return None


def refresh_gate_overrides(flights):
    for flight in flights:
        source_gate = exact_gate(flight.get("source_gate", flight.get("gate")))
        flight.setdefault("source_gate", source_gate)
        flight["gate"] = manual_gate(flight["flight_no"]) or source_gate or UNKNOWN_GATE
    return flights

def get_now_ist() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3)

def parse_iso_dt(val):
    if not val or not isinstance(val, str):
        return None
    try:
        clean = val.replace(" ", "T")
        if "+" in clean: clean = clean.split("+")[0]
        if clean.endswith("Z"): clean = clean[:-1]
        return datetime.fromisoformat(clean)
    except Exception:
        return None

def calc_gate_dist(g1: str, g2: str):
    """İki kapı arası gerçekçi yürüyüş süresi ve etiket"""
    if not g1 or not g2 or any(k in g1 for k in ["Belirsiz", "Bölgesi"]) or any(k in g2 for k in ["Belirsiz", "Bölgesi"]):
        return 12, "Standart (~12 dk)"

    p1 = g1[0].upper()
    p2 = g2[0].upper()
    n1_match = re.findall(r"\d+", g1)
    n2_match = re.findall(r"\d+", g2)

    # Net kapı numarası henüz yoksa (örn: F İskelesi)
    if "İskele" in g1 or "İskele" in g2 or not n1_match or not n2_match:
        if p1 == p2:
            return 2, "Aynı İskele (~2 dk)"
        pair = {p1, p2}
        if pair in [{"E", "F"}, {"A", "B"}]:
            return 7, "Komşu İskele (~7 dk)"
        elif "D" in pair:
            return 10, "Merkez D (~10 dk)"
        elif (p1 in {"E", "F"} and p2 in {"A", "B"}) or (p1 in {"A", "B"} and p2 in {"E", "F"}):
            return 22, "Uzak Blok (~22 dk)"
        elif "G" in pair:
            return 20, "İç Hatlar (~20 dk)"
        return 12, "Standart (~12 dk)"

    n1 = int(n1_match[0])
    n2 = int(n2_match[0])

    if p1 == p2:
        diff = abs(n1 - n2)
        if diff == 0:
            return 0, "Kapındasın (0 dk)"
        elif diff <= 2:
            return 1, f"Bitişik ({diff} kapı | ~1 dk)"
        elif diff <= 5:
            return 3, f"Aynı İskele ({diff} kapı | ~3 dk)"
        else:
            return 6, f"Aynı İskele (~6 dk)"

    pair = {p1, p2}
    if pair in [{"E", "F"}, {"A", "B"}]:
        return 7, "Komşu İskele (~7 dk)"
    elif "D" in pair:
        return 10, "Merkez D (~10 dk)"
    elif (p1 in {"E", "F"} and p2 in {"A", "B"}) or (p1 in {"A", "B"} and p2 in {"E", "F"}):
        return 22, "Uzak Blok (~22 dk)"
    elif "G" in pair:
        return 20, "İç Hatlar (~20 dk)"
    return 12, "Standart (~12 dk)"

def calc_transfer_metrics(arr_gate: str, dep_gate: str, rem_now_min: float):
    walk_min, tag = calc_gate_dist(arr_gate, dep_gate)
    safety_margin = rem_now_min - walk_min - 20
    if safety_margin <= 15:
        risk_str = f"🚨 ÇOK ACİL (Pay: {int(safety_margin)} dk)"
        risk_level = 1
    elif safety_margin <= 35:
        risk_str = f"⚠️ DİKKAT (Pay: {int(safety_margin)} dk)"
        risk_level = 2
    else:
        risk_str = f"✅ RAHAT (Pay: {int(safety_margin)} dk)"
        risk_level = 3
    return walk_min, tag, risk_str, risk_level

def query_wa_bridge_gate(flight_no: str, date=None, direction="arrival"):
    """Only return a verified full gate; a pier or timeout is not a gate."""
    result = bridge_client.lookup(flight_no, date=date, direction=direction)
    return result.get("gate") if result.get("success") else None


def fetch_iga_direct_flights():
    global cached_flights
    now_ist = get_now_ist()

    if time.time() - cached_flights["time"] < 40 and cached_flights["arrivals"]:
        return now_ist, refresh_gate_overrides(cached_flights["arrivals"]), refresh_gate_overrides(cached_flights["departures"]), cached_flights["source"]

    url = "https://wild-lake-8cfa.haciyatmaz300.workers.dev/"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    def _post(nature, page_size=40, start_date="", button=""):
        payload = {
            "nature": str(nature),
            "searchTerm": "",
            "pageSize": str(page_size),
            "isInternational": "1",
            "date": start_date,
            "endDate": "",
            "culture": "tr",
            "clickedButton": button
        }
        encoded = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=encoded,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            }
        )
        with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    arrivals = []
    seen_arr = set()
    try:
        start_arr_iso = (now_ist - timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%S")
        data_arr = _post(nature=0, page_size=50, start_date=start_arr_iso)
        raw_arr = data_arr.get("result", {}).get("data", {}).get("flights", [])
        last_arr_date = raw_arr[-1].get("scheduledDatetime") if raw_arr else ""
        if last_arr_date:
            try:
                data_arr2 = _post(nature=0, page_size=50, start_date=last_arr_date, button="moreFlight")
                raw_arr.extend(data_arr2.get("result", {}).get("data", {}).get("flights", []))
            except Exception:
                pass

        # Gece yarısı geçişi: Saat 20:00'den sonraysa ertesi günün ilk gelişlerini de ekle
        if now_ist.hour >= 20:
            tomorrow_arr_start = (now_ist + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
            try:
                data_arr_tom = _post(nature=0, page_size=50, start_date=tomorrow_arr_start)
                raw_arr.extend(data_arr_tom.get("result", {}).get("data", {}).get("flights", []))
            except Exception:
                pass
        elif now_ist.hour < 2:
            yesterday_arr_start = (now_ist - timedelta(days=1)).strftime("%Y-%m-%dT23:00:00")
            try:
                data_arr_yest = _post(nature=0, page_size=50, start_date=yesterday_arr_start)
                raw_arr.extend(data_arr_yest.get("result", {}).get("data", {}).get("flights", []))
            except Exception:
                pass

        for item in raw_arr:
            origin_iata = str(item.get("fromCityCode") or "").strip().upper()
            arr_dt = parse_iso_dt(item.get("estimatedDatetime")) or parse_iso_dt(item.get("scheduledDatetime"))
            if arr_dt:
                raw_flight_no = str(item.get("flightNumber") or "TK").strip().upper()
                flight_no = normalize_flight(raw_flight_no)
                dedup_key = f"{origin_iata}_{arr_dt.strftime('%Y%m%d%H%M')}_{flight_no}"
                if dedup_key not in seen_arr:
                    seen_arr.add(dedup_key)
                    gate_raw = str(item.get("gate") or "").strip()
                    carousel = str(item.get("carousel") or "").strip()

                    gate_val = manual_gate(flight_no) or manual_gate(raw_flight_no) or exact_gate(gate_raw) or UNKNOWN_GATE

                    arrivals.append({
                        "flight_no": flight_no,
                        "raw_flight_no": raw_flight_no,
                        "origin_name": str(item.get("fromCityName", "")).strip(),
                        "origin_iata": origin_iata,
                        "is_oss": origin_iata in OSS_AIRPORTS,
                        "arr_time": arr_dt,
                        "gate": gate_val,
                        "source_gate": exact_gate(gate_raw),
                        "carousel": carousel,
                        "status": item.get("remark") or "Planlandı"
                    })
    except Exception as e:
        print(f"[!] Geliş hatası: {e}", flush=True)

    departures = []
    seen_dep = set()
    try:
        start_dep_iso = (now_ist - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
        data_dep = _post(nature=1, page_size=50, start_date=start_dep_iso)
        raw_dep = data_dep.get("result", {}).get("data", {}).get("flights", [])
        last_date = raw_dep[-1].get("scheduledDatetime") if raw_dep else ""
        if last_date:
            try:
                data_dep2 = _post(nature=1, page_size=50, start_date=last_date, button="moreFlight")
                raw_dep.extend(data_dep2.get("result", {}).get("data", {}).get("flights", []))
            except Exception:
                pass

        # Gece yarısı geçişi (Midnight Rollover): 20:00'den sonraysa ertesi günün ilk uçuşlarını da çek
        if now_ist.hour >= 20:
            tomorrow_dep_start = (now_ist + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
            try:
                data_dep_tom = _post(nature=1, page_size=50, start_date=tomorrow_dep_start)
                raw_dep_tom = data_dep_tom.get("result", {}).get("data", {}).get("flights", [])
                raw_dep.extend(raw_dep_tom)
                last_tom = raw_dep_tom[-1].get("scheduledDatetime") if raw_dep_tom else ""
                if last_tom:
                    try:
                        data_dep_tom2 = _post(nature=1, page_size=50, start_date=last_tom, button="moreFlight")
                        raw_dep.extend(data_dep_tom2.get("result", {}).get("data", {}).get("flights", []))
                    except Exception:
                        pass
            except Exception:
                pass
        elif now_ist.hour < 2:
            yesterday_dep_start = (now_ist - timedelta(days=1)).strftime("%Y-%m-%dT23:00:00")
            try:
                data_dep_yest = _post(nature=1, page_size=50, start_date=yesterday_dep_start)
                raw_dep.extend(data_dep_yest.get("result", {}).get("data", {}).get("flights", []))
            except Exception:
                pass

        for item in raw_dep:
            dest_iata = str(item.get("toCityCode") or "").strip().upper()
            dep_dt = parse_iso_dt(item.get("scheduledDatetime")) or parse_iso_dt(item.get("estimatedDatetime"))
            gate = str(item.get("gate") or "").strip()
            gate_val = exact_gate(gate) or UNKNOWN_GATE

            if CONFIG["only_international"] and gate_val.startswith("G"):
                continue

            if dep_dt:
                flight_no = normalize_flight(item.get("flightNumber", "TK"))
                dedup_key = f"{dest_iata}_{dep_dt.strftime('%Y%m%d%H%M')}_{flight_no}"
                if dedup_key not in seen_dep:
                    seen_dep.add(dedup_key)
                    departures.append({
                        "flight_no": flight_no,
                        "dest": str(item.get("toCityName", "")).strip(),
                        "dest_iata": dest_iata,
                        "dep_time": dep_dt,
                        "gate": gate_val,
                        "source_gate": exact_gate(gate),
                        "counter": item.get("counter", ""),
                        "status": item.get("remark", "")
                    })
    except Exception as e:
        print(f"[!] Gidiş hatası: {e}", flush=True)

    arrivals.sort(key=lambda x: x["arr_time"])
    departures.sort(key=lambda x: x["dep_time"])

    cached_flights = {"time": time.time(), "arrivals": arrivals, "departures": departures, "source": "iGA Canlı FIDS"}
    return now_ist, arrivals, departures, "iGA Canlı FIDS"

def ask_gpt4o_mini(user_msg: str, chat_id=None) -> str:
    """Azure OpenAI GPT-4o-mini ile anlık canlı uçuş verisini yorumlayan akıllı asistan"""
    now_ist, arrivals, departures, _ = fetch_iga_direct_flights()

    ctx_lines = [f"Canlı Saat: {now_ist.strftime('%H:%M')} | Operatör Kapısı: {CONFIG.get('user_gate', 'F3')}"]

    # Mesajda geçen uçuş kodu, kapı veya şehir varsa eşleşenleri bul
    user_upper = user_msg.upper()
    matched = []
    codes = re.findall(r"(?:TK\s*\d+|\b[A-Z]{3}\b)", user_upper)
    gates = re.findall(r"\b([A-G]\d+[A-Z]?)\b", user_upper)
    for f in arrivals + departures:
        fn = f["flight_no"].replace(" ", "")
        city = (f.get("origin_name") or f.get("dest") or "").upper()
        g = f.get("gate", "").upper()
        if any(c.replace(" ", "") in fn for c in codes) or any(w in city for w in user_upper.split() if len(w) > 3) or any(gate in g for gate in gates):
            matched.append(f)

    if matched:
        ctx_lines.append("İlgili Uçuşlar:")
        for m in matched[:5]:
            if "arr_time" in m:
                ctx_lines.append(f"• GELİŞ: {m['flight_no']} ({m.get('origin_name')}) | Kapı: {m['gate']} | İniş: {m['arr_time'].strftime('%H:%M')} | Durum: {m['status']}")
            else:
                ctx_lines.append(f"• GİDİŞ: {m['flight_no']} ({m.get('dest')}) | Kapı: {m['gate']} | Kalkış: {m['dep_time'].strftime('%H:%M')} | Durum: {m['status']}")

    arr_brief = [f"{a['flight_no']} ({a['origin_name']}, Kapı {a['gate']}, {a['arr_time'].strftime('%H:%M')})" for a in arrivals[:8]]
    dep_brief = [f"{d['flight_no']}->{d['dest']} (Kapı {d['gate']}, {d['dep_time'].strftime('%H:%M')})" for d in departures[:8]]
    ctx_lines.append("Yakın Gelişler (İnişler): " + "; ".join(arr_brief))
    ctx_lines.append("Yakın Kalkışlar (Gidişler): " + "; ".join(dep_brief))

    system_prompt = (
        "Sen İstanbul Havalimanı (IST) WCHS / PRM tekerlekli sandalye transfer operasyon asistanısın. "
        "Operatör sahada yolcu yetiştiriyor ve acelesi var. "
        "DİKKAT: Kullanıcı bir kapı sorduğunda (örn: F3, F6, E2), bu kapıyı ASLA sadece gidiş olarak varsayma! "
        "WCHS operasyonunda öncelik GELEN yolcuyu karşılamaktır. Eğer kapıda gelen/inen uçak varsa mutlaka önce onu belirt, ardından kalkan uçağı söyle. "
        "Tam kapı doğrulanmamışsa bunu açıkça söyle. İskele, E/F bölgesi veya bagaj bandından kapı tahmin etme; bir iskeleye yönlendirme. "
        "Yanıtların: Çok kısa, net, saygılı, doğrudan aksiyon odaklı Türkçe olmalı. Maksimum 2 cümle."
    )
    ctx_text = "\n".join(ctx_lines)
    user_content = f"Sistem Durumu:\n{ctx_text}\n\nOperatör Mesajı: {user_msg}"

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        "max_tokens": 120,
        "temperature": 0.2
    }
    try:
        req = urllib.request.Request(
            OPENAI_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "api-key": OPENAI_KEY}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"⚠️ Asistan yanıt veremedi ({e})."

def get_main_keyboard():
    curr = CONFIG.get("user_gate", "F3")
    return {
        "keyboard": [
            [{"text": f"📍 Kapım ({curr})"}, {"text": "🛫 Gidişler"}],
            [{"text": "📊 Durum"}, {"text": "❓ Yardım"}]
        ],
        "resize_keyboard": True
    }

def send_telegram(text: str, reply_markup=None, target_chat_id=None):
    cid = target_chat_id or TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": reply_markup or get_main_keyboard()
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8")).get("result", {}).get("message_id")
    except Exception as e:
        print(f"[!] Telegram gönderim hatası: {e}", flush=True)

def update_telegram(text, message_id, chat_id=None):
    if not message_id:
        return send_telegram(text, target_chat_id=chat_id)
    payload = {"chat_id": chat_id or TELEGRAM_CHAT_ID, "message_id": message_id,
               "text": text, "parse_mode": "HTML"}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
        data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception:
        # Duplicate edits are rejected by Telegram, but are harmless.
        pass
    return message_id


def render_gate_radar(user_gate, arrivals, departures, note=""):
    gate = exact_gate(user_gate)
    if not gate:
        return "⚠️ Geçersiz kapı formatı. Örn: A11 veya B5A"

    now_ist = get_now_ist()
    is_base = gate[-1].isdigit()
    relevant_gates = [gate, gate + "A", gate + "B"] if is_base else [gate]

    sections = []
    has_any_flight = False

    for g in relevant_gates:
        matched_arrs = {}
        for f in arrivals:
            if exact_gate(f.get("gate")) == g and -60 <= (f["arr_time"] - now_ist).total_seconds() / 60 <= 120:
                matched_arrs[f["flight_no"]] = f

        now_mono = time.monotonic()
        for f in list(confirmed_arrival_store.values()):
            if exact_gate(f.get("gate")) == g and now_mono - f.get("time", 0) < GATE_CACHE_TTL:
                matched_arrs[f["flight_no"]] = f

        g_arrs = list(matched_arrs.values())
        g_arrs.sort(key=lambda f: abs((f["arr_time"] - now_ist).total_seconds()))

        g_deps = [f for f in departures if exact_gate(f.get("gate")) == g
                  and -40 <= (f["dep_time"] - now_ist).total_seconds() / 60 <= 100]
        g_deps.sort(key=lambda f: abs((f["dep_time"] - now_ist).total_seconds()))

        if g_arrs or g_deps:
            has_any_flight = True
            lines = [f"🚪 <b>{g}</b>"]
            if g_arrs:
                for f in g_arrs[:3]:
                    lines.append(f"🛬 Geliş: <b>{html.escape(f['flight_no'])}</b> · "
                                 f"{html.escape(f.get('origin_name', ''))} · {f['arr_time']:%H:%M}")
            else:
                lines.append("🛬 Geliş: Doğrulanmış uçuş bilgisi yok.")

            if g_deps:
                for f in g_deps[:3]:
                    status_suffix = f" ({f['status']})" if f.get("status") else ""
                    lines.append(f"🛫 Gidiş: <b>{html.escape(f['flight_no'])}</b> · "
                                 f"{html.escape(f.get('dest', ''))} · {f['dep_time']:%H:%M}{status_suffix}")
            else:
                lines.append("🛫 Gidiş: Doğrulanmış uçuş bilgisi yok.")

            sections.append("\n".join(lines))

    if not has_any_flight:
        lines = [f"🚪 <b>{gate}</b>",
                 "🛬 Geliş: Doğrulanmış uçuş bilgisi yok.",
                 "🛫 Gidiş: Doğrulanmış uçuş bilgisi yok."]
        sections.append("\n".join(lines))
    elif not is_base:
        base_gate = re.sub(r"[A-Z]+$", "", gate)
        base_flights = [f for f in arrivals + departures if exact_gate(f.get("gate")) == base_gate
                        and -40 <= (f.get("arr_time", f.get("dep_time")) - now_ist).total_seconds() / 60 <= 100]
        if base_flights:
            sections.append(f"ℹ️ {base_gate} Ana Kapı: " + ", ".join(html.escape(f['flight_no']) for f in base_flights[:3]))

    # 🚖 Potansiyel Aktarma / Taksi Gidişleri (İlk 2 Saat)
    CLOSED_STATUSES = {"kapı kapandı", "kapi kapandi", "iptal", "kalktı", "kalkti", "uçak kalktı", "ucak kalkti", "gitti"}
    pot_deps = []
    for d in departures:
        dep_g = exact_gate(d.get("gate"))
        if not dep_g or dep_g.startswith("G") or dep_g in relevant_gates:
            continue
        st = str(d.get("status") or "").strip().lower()
        if st in CLOSED_STATUSES:
            continue
        rem_min = (d["dep_time"] - now_ist).total_seconds() / 60.0
        if 15 <= rem_min <= 130:
            w_min, dist_tag = calc_gate_dist(gate, dep_g)
            pot_deps.append((rem_min, d, dep_g, w_min))

    pot_deps.sort(key=lambda x: x[0])

    pot_section = []
    if pot_deps:
        pot_section.append("🚖 <b>Potansiyel Gidişler (İlk 2 Saat):</b>")
        for rem_min, d, dep_g, w_min in pot_deps[:4]:
            status_str = f" · <i>{d['status']}</i>" if d.get("status") else ""
            pot_section.append(
                f"• 🚪 <b>{dep_g}</b> ➔ <b>{d['flight_no']}</b> · {html.escape(d['dest'])} · "
                f"<b>{d['dep_time']:%H:%M}</b> ({int(rem_min)} dk{status_str}) [~{w_min} dk taksi]"
            )
        pot_section.append("💡 <i>Tüm liste için aşağıdaki <b>🛫 Gidişler</b> butonuna basabilirsin.</i>")

    header = f"📍 <b>{gate}</b> | 🕒 {now_ist:%H:%M}"
    parts = [header] + sections
    if pot_section:
        parts.append("\n".join(pot_section))
    if note:
        parts.append(html.escape(note))
    return "\n\n".join(parts)


def execute_proximity_radar(user_gate="F3", chat_id=None):
    user_gate = exact_gate(user_gate)
    if not user_gate:
        send_telegram("Kapıyı B5 veya B5A biçiminde yazabilirsin.", target_chat_id=chat_id)
        return
    CONFIG["user_gate"] = user_gate
    now_ist, arrivals, departures, _ = fetch_iga_direct_flights()
    send_telegram(render_gate_radar(user_gate, arrivals, departures), target_chat_id=chat_id)


def execute_departures_radar(pier=None, chat_id=None):
    now_ist, _, departures, _ = fetch_iga_direct_flights()
    curr_gate = CONFIG.get("user_gate", "F3")
    CLOSED_STATUSES = {"kapı kapandı", "kapi kapandi", "iptal", "kalktı", "kalkti", "uçak kalktı", "ucak kalkti", "gitti"}

    active_deps = []
    for d in departures:
        gate = exact_gate(d.get("gate"))
        if not gate or gate.startswith("G"):
            continue
        if pier and not gate.startswith(pier):
            continue
        st = str(d.get("status") or "").strip().lower()
        if st in CLOSED_STATUSES:
            continue
        rem_min = (d["dep_time"] - now_ist).total_seconds() / 60.0
        if 5 <= rem_min <= 140:
            w_min, dist_tag = calc_gate_dist(curr_gate, gate)
            active_deps.append((rem_min, d, gate, w_min, dist_tag))

    active_deps.sort(key=lambda x: x[0])

    pier_title = f"{pier} İskelesi " if pier else ""
    header = (
        f"🛫 <b>DIŞ HATLAR GİDİŞ RADARI ({pier_title}İlk 2 Saat)</b>\n"
        f"🕒 Saat: <b>{now_ist.strftime('%H:%M')}</b> | 📍 Konumun: <b>{curr_gate}</b>\n"
        f"───────────────────────\n"
    )

    if not active_deps:
        msg = header + f"<i>Önümüzdeki 2 saat içinde {pier_title}kapısı belli aktif gidiş bulunamadı.</i>"
        send_telegram(msg, target_chat_id=chat_id)
        return

    lines = []
    for rem_min, d, gate, w_min, dist_tag in active_deps[:14]:
        status_suffix = f" · <i>{d['status']}</i>" if d.get("status") else ""
        lines.append(
            f"• 🚪 <b>{gate}</b> ➔ <b>{d['flight_no']}</b> · {html.escape(d['dest'])} · "
            f"<b>{d['dep_time']:%H:%M}</b> (<b>{int(rem_min)} dk</b>{status_suffix}) [~{w_min} dk taksi]"
        )

    footer = f"\n\n💡 <i>İskele bazlı hızlı filtre için: <code>A Gidiş</code>, <code>B Gidiş</code>, <code>D Gidiş</code>, <code>F Gidiş</code> yazabilirsin.</i>"
    send_telegram(header + "\n".join(lines) + footer, target_chat_id=chat_id)


def background_arrival_gate_crawler():
    """
    7/24 Arka Plan Geliş Kapı Tarayıcısı (Rolling Window Pre-fetcher).
    - SADECE Dış Hatlar Geliş Uçuşları (isInternational=1, nature=0)
    - Zaman Aralığı: Teker koymuş (-25 dk) ila inmek üzere olan (+20 dk) uçuşlar
    - WhatsApp köprüsünü yormadan her 25 saniyede en fazla 1 uçuş sorgular
    - Doğrulanan kapıları 30 dakika boyunca hafızada (custom_flight_gates) tutar
    """
    print("[*] WCHS Background Arrival Gate Crawler aktif.", flush=True)
    recently_queried = {}

    while True:
        try:
            now_ist, arrivals, _, _ = fetch_iga_direct_flights()
            now_mono = time.monotonic()

            candidates = []
            for f in arrivals:
                arr_time = f.get("arr_time")
                if not arr_time:
                    continue

                delta_min = (arr_time - now_ist).total_seconds() / 60.0
                # iGA WhatsApp kapı bilgisini uçak teker koyduktan sonra girer.
                # Son 75 dk içinde inmiş veya inmek üzere olan uçuşları sorguluyoruz!
                is_landed = (f.get("status") == "İndi" and delta_min >= -75) or (-45 <= delta_min <= 2)
                if not is_landed:
                    continue

                flight_no = f.get("flight_no") or ""
                # SADECE Türk Hava Yolları (TK) Dış Hatlar Gelişleri!
                if not flight_no.startswith("TK"):
                    continue

                # SADECE Avrupa / Birleşik Krallık / ABD-Kanada (OSS - Uygulama Olan) Uçuşlar!
                if not f.get("is_oss"):
                    continue

                # FIDS'te zaten doğrulanmış kapı var mı?
                if exact_gate(f.get("source_gate")):
                    continue

                # Hafızada zaten doğrulanmış kapı var mı?
                if manual_gate(flight_no):
                    continue

                # Cooldown: son 3 dakika içinde sorgulanmış mı?
                if now_mono - recently_queried.get(flight_no, 0) < 180:
                    continue

                candidates.append((abs(delta_min), f))

            if candidates:
                status = bridge_client.status()
                if status.get("liveAgentActive"):
                    crawler_stats["last_status"] = "Canlı Destek Devrede (Durduruldu)"
                    print("[!] [Crawler] Canlı destek devrede. iGA görevlisine mesaj gitmemesi için 120 sn bekleniyor...", flush=True)
                    time.sleep(120)
                    continue

                candidates.sort(key=lambda x: x[0])
                _, target_flight = candidates[0]
                flight_no = target_flight["flight_no"]
                query_flight = target_flight.get("raw_flight_no") or flight_no
                flight_date = target_flight["arr_time"].date().isoformat()
                recently_queried[flight_no] = now_mono
                recently_queried[query_flight] = now_mono

                if status.get("connected"):
                    crawler_stats["total_queries"] += 1
                    crawler_stats["last_flight"] = query_flight
                    print(f"[*] [Crawler] iGA WhatsApp sorgulanıyor: {query_flight} ({flight_date})...", flush=True)
                    res = bridge_client.lookup(query_flight, date=flight_date, direction="arrival")
                    if res.get("status") in ["live_agent_active", "live_agent_redirect"]:
                        crawler_stats["last_status"] = "Canlı Destek Devrede (Durduruldu)"
                        print(f"[!] [Crawler] Canlı destek devrede algılandı: {res.get('error')}. 300 sn bekleniyor...", flush=True)
                        time.sleep(300)
                        continue

                    if res.get("success") and res.get("gate"):
                        gate = exact_gate(res.get("gate"))
                        if gate:
                            set_manual_gate(flight_no, gate, origin_name=target_flight.get("origin_name", ""), arr_time=target_flight.get("arr_time"))
                            set_manual_gate(query_flight, gate, origin_name=target_flight.get("origin_name", ""), arr_time=target_flight.get("arr_time"))
                            crawler_stats["confirmed_gates"] += 1
                            crawler_stats["last_status"] = f"Doğrulandı ({gate})"
                            print(f"[+] [Crawler] Kapı doğrulandı: {query_flight} -> {gate}", flush=True)
                    else:
                        st = res.get("status", "Açıklanmadı")
                        crawler_stats["last_status"] = f"Kapı yok ({st})"
                        print(f"[-] [Crawler] {flight_no} kapı henüz açıklanmamış: {st}", flush=True)
                else:
                    crawler_stats["last_status"] = "Köprü bağlı değil"
                    print(f"[!] [Crawler] WhatsApp köprüsü henüz bağlı değil, bekleniyor...", flush=True)
                    time.sleep(15)
                    continue

                # Güvenli tempo: iGA botunu boğmamak ve canlı desteğe yönlenmeyi önlemek için sorgular arası 75 saniye dinlen
                time.sleep(75)
            else:
                time.sleep(15)

        except Exception as err:
            print(f"[!] [Crawler Döngü Hatası]: {err}", flush=True)
            time.sleep(15)


def start_background_crawler():
    crawler_thread = threading.Thread(target=background_arrival_gate_crawler, daemon=True, name="ArrivalCrawlerThread")
    crawler_thread.start()
    print("[*] Arrival Gate Crawler thread started.", flush=True)
    return crawler_thread


def execute_radar(custom_gate=None, target_flight=None, chat_id=None):
    """Sade ve okunabilir Aktarma Radarı"""
    now_ist, arrivals, departures, _ = fetch_iga_direct_flights()

    if target_flight:
        clean_tf = normalize_flight(target_flight)
        header = f"🎯 <b>UÇUŞ KARTI: {clean_tf}</b> | 🕒 <b>{now_ist.strftime('%H:%M')}</b>\n───────────────────────\n"
        
        # 1. Geliş Uçuşlarında Ara
        arr_matches = [a for a in arrivals if normalize_flight(a["flight_no"]) == clean_tf]
        if arr_matches:
            arr = arr_matches[0]
            arr_gate = manual_gate(clean_tf) or custom_gate or arr["gate"]
            diff_now = int((arr["arr_time"] - now_ist).total_seconds() / 60)
            status_text = f"İndi ({abs(diff_now)} dk önce)" if diff_now <= 0 else f"İniş: {arr['arr_time'].strftime('%H:%M')} ({diff_now} dk sonra)"
            oss_str = " | 🇪🇺 OSS" if arr["is_oss"] else ""

            best_conn = None
            for dep in departures:
                delta_arr = (dep["dep_time"] - arr["arr_time"]).total_seconds() / 60
                rem_now_min = (dep["dep_time"] - now_ist).total_seconds() / 60
                w_min, tag, risk_str, risk_level = calc_transfer_metrics(arr_gate, dep["gate"], rem_now_min)
                if 35 <= delta_arr <= 200 and rem_now_min >= (w_min + 15):
                    if best_conn is None or risk_level < best_conn[0] or (risk_level == best_conn[0] and rem_now_min < best_conn[1]):
                        best_conn = (risk_level, rem_now_min, dep, w_min, tag, risk_str)

            msg = (
                f"🛬 <b>GELİŞ: {arr['flight_no']} ({arr['origin_name']})</b>\n"
                f"🚪 Kapı: <b>{arr_gate}</b>\n"
                f"🕒 {status_text}{oss_str} | Durum: {arr['status']}\n"
            )
            if best_conn:
                msg += (
                    f"\n↳ <b>Önerilen Aktarma:</b> {best_conn[2]['flight_no']} ➔ {best_conn[2]['dest']}\n"
                    f"   Kapı: <b>{best_conn[2]['gate']}</b> | Kalkış: <b>{best_conn[2]['dep_time'].strftime('%H:%M')}</b> ({int(best_conn[1])} dk kaldı)\n"
                    f"   İntikal: ~{best_conn[3]} dk | {best_conn[5]}"
                )
            send_telegram(header + msg, target_chat_id=chat_id)
            return

        # 2. Gidiş Uçuşlarında Ara
        dep_matches = [d for d in departures if normalize_flight(d["flight_no"]) == clean_tf]
        if dep_matches:
            dep = dep_matches[0]
            dep_gate = manual_gate(clean_tf) or custom_gate or dep["gate"]
            rem_min = int((dep["dep_time"] - now_ist).total_seconds() / 60)
            user_g = CONFIG.get("user_gate", "F3")
            w_min, dist_tag = calc_gate_dist(user_g, dep_gate)
            msg = (
                f"🛫 <b>GİDİŞ: {dep['flight_no']} ➔ {dep['dest']}</b>\n"
                f"🚪 Kapı: <b>{dep_gate}</b> ({dist_tag})\n"
                f"🕒 Kalkış: <b>{dep['dep_time'].strftime('%H:%M')}</b> (<b>{rem_min} dk</b> kaldı)\n"
                f"📋 Durum: {dep['status']} | Kontuar: {dep.get('counter', '-')}"
            )
            send_telegram(header + msg, target_chat_id=chat_id)
            return

        # FIDS'te henüz yer almayan ama WhatsApp'tan çekilen uçuşlar
        gate_info = custom_gate or manual_gate(clean_tf)
        if gate_info:
            send_telegram(header + f"🎯 <b>{clean_tf}</b> iGA WhatsApp Kapısı: <b>{gate_info}</b>", target_chat_id=chat_id)
        else:
            send_telegram(header + f"⚠️ <b>{clean_tf}</b> güncel uçuş tablosunda bulunamadı.", target_chat_id=chat_id)
        return

    curr = CONFIG.get("user_gate", "F3")
    execute_proximity_radar(user_gate=curr, chat_id=chat_id)

def parse_user_intent(raw_text: str):
    t = raw_text.strip()
    t_clean = t.upper().replace("İ", "I").replace("Ş", "S").replace("Ç", "C").replace("Ğ", "G").replace("Ö", "O").replace("Ü", "U")

    if "TK" in t_clean:
        m_fg = re.search(r"(TK\s*\d+).*?\b([A-G])\s*(\d+[A-Z]?)\b", t_clean)
        if m_fg:
            flight = normalize_flight(m_fg.group(1))
            gate = f"{m_fg.group(2)}{m_fg.group(3)}"
            return "FLIGHT_GATE", flight, gate

        m_f = re.search(r"\b(TK\s*\d+)\b", t_clean)
        if m_f:
            flight = normalize_flight(m_f.group(1))
            return "SINGLE_FLIGHT", flight, None

    m_prox = re.search(
        r"\b([A-G])\s*[-_]?\s*(\d{1,2})([A-Z])?(?:['’`]?\s*(?:DEYIM|DAYIM|TEYIM|TAYIM|DEYIZ|DAYIZ|TEYIZ|TAYIZ|DE|DA|TE|TA))?\b",
        t_clean
    )
    if m_prox:
        pier = m_prox.group(1).upper()
        num = m_prox.group(2)
        sub = (m_prox.group(3) or "").upper()
        gate = f"{pier}{num}{sub}"
        return "PROXIMITY", gate, None

    # İskele bazlı gidiş sorguları: "A GİDİŞ", "B GİDİŞLER", "D GİDİŞİ", "F KALKIŞ"
    m_pier_dep = re.search(r"\b([A-G])\s*(?:ISKELE|ISKELESI)?\s*(?:GIDIS|GIDISLER|KALKIS|KALKISLAR)\b", t_clean)
    if m_pier_dep:
        return "DEPARTURES_PIER", m_pier_dep.group(1).upper(), None

    t_lower = t.lower()
    if any(k in t_lower for k in ["gidiş", "gidis", "kalkış", "kalkis", "/gidisler", "/gidis"]):
        return "DEPARTURES", None, None

    if any(k in t_lower for k in ["iniş", "inis", "gelen", "şuanki", "şu anki", "şuan"]):
        return "ARRIVALS", None, None

    return "AI_QUERY", None, None

def process_telegram_message(msg):
    try:
        handle_telegram_message(msg)
    except Exception as exc:
        print(f"[!] Telegram sorgusu tamamlanamadı: {type(exc).__name__}", flush=True)
        send_telegram("⚠️ Sorgu tamamlanamadı. Yeniden deneyebilirsin.", target_chat_id=str(msg.get("chat", {}).get("id", TELEGRAM_CHAT_ID)))
    finally:
        telegram_slots.release()


def handle_telegram_message(msg):
    raw_text = msg.get("text", "").strip()
    chat_id = str(msg.get("chat", {}).get("id", TELEGRAM_CHAT_ID))

    if not raw_text:
        return

    if raw_text in ["/start", "/help", "/yardim", "❓ Yardım", "yardım"]:
        curr = CONFIG.get("user_gate", "F3")
        welcome = (
            "👋 <b>WCHS-IST Transfer & Taksi Asistanı</b>\n\n"
            "Tekerlekli sandalye & taksi (buggy) hizmeti için optimize edildi:\n\n"
            "🚪 <b>Kapı Sorgula (Geliş + Potansiyel Gidişler):</b>\n"
            "• <code>b5</code>, <code>a11</code>, <code>f3</code>, <code>b5a</code>\n"
            "• <code>ben f4deyim</code> (konumunu kaydeder ve kapıyı listeler)\n\n"
            "🛫 <b>Dış Hat Gidiş Radarı (İlk 2 Saat):</b>\n"
            "• Menüdeki <b>🛫 Gidişler</b> butonuna bas\n"
            "• İskele için: <code>a gidiş</code>, <code>b gidiş</code>, <code>d gidiş</code>, <code>f gidiş</code>\n\n"
            "✈️ <b>Uçuş Sorgula:</b>\n"
            "• <code>TK0630</code>, <code>TK1826</code> (kapı, iniş/kalkış ve aktarma kartı)\n\n"
            "✏️ <b>Manuel Kapı Kaydet:</b>\n"
            "• <code>TK1234 B5</code>\n\n"
            "📊 <b>Canlı Hafıza & Sayaç:</b>\n"
            "• <code>/durum</code> veya menüdeki <b>📊 Durum</b> butonu\n\n"
            f"📍 <i>Şu an kayıtlı kapın: <b>{curr}</b></i>"
        )
        send_telegram(welcome, target_chat_id=chat_id)
        return

    elif raw_text in ["/durum", "/hafiza", "/stats", "📊 Durum", "🧠 Hafıza"]:
        now_ist, _, _, _ = fetch_iga_direct_flights()
        now_mono = time.monotonic()

        cached_items = []
        for fn, g in list(custom_flight_gates.items()):
            t = custom_gate_times.get(fn, now_mono)
            elapsed = now_mono - t
            if elapsed < GATE_CACHE_TTL:
                rem_min = int((GATE_CACHE_TTL - elapsed) // 60)
                cached_items.append(f"• <b>{fn}</b> ➔ Kapı: <b>{g}</b> ({rem_min} dk kaldı)")

        bridge_st = bridge_client.status()
        active_f = bridge_st.get("active")
        active_str = f"{active_f.get('flight')} ({active_f.get('state')})" if active_f else "Boşta"

        msg = (
            f"🧠 <b>WCHS Radar Hafıza & Canlı İstatistik</b>\n"
            f"🕒 Saat: <b>{now_ist.strftime('%H:%M:%S')}</b>\n"
            f"───────────────────────\n"
            f"🚪 <b>Doğrulanmış Kapılar ({len(cached_items)} Uçuş):</b>\n"
            f"{chr(10).join(cached_items) if cached_items else '<i>Şu an hafızada doğrulanmış geliş kapısı yok.</i>'}\n\n"
            f"📊 <b>Arka Plan Tarayıcısı:</b>\n"
            f"• Toplam Sorgu: <b>{crawler_stats['total_queries']}</b>\n"
            f"• Doğrulanan Kapı: <b>{crawler_stats['confirmed_gates']}</b>\n"
            f"• Son İncelenen: <b>{crawler_stats.get('last_flight') or '-'}</b> ({crawler_stats.get('last_status') or '-'})\n\n"
            f"📡 <b>WhatsApp Köprüsü:</b> {'🟢 Bağlı' if bridge_st.get('connected') else '🔴 Bağlı Değil'}\n"
            f"↳ Aktif İşlem: <b>{active_str}</b>\n"
            f"↳ Sırada: <b>{bridge_st.get('queued', 0)}</b>\n"
            f"↳ Canlı Destek Kilidi: {'🔴 DEVREDE (Mesaj Gönderimi Durduruldu)' if bridge_st.get('liveAgentActive') else '🟢 Pasif (Normal Akış)'}\n\n"
            f"⏳ <b>Hafıza Temizleme Süreleri (TTL):</b>\n"
            f"• Doğrulanan Kapı: <b>30 Dakika (1800 sn)</b> sonra silinir.\n"
            f"• Açıklanmamış Kapı: <b>3 Dakika (180 sn)</b> sonra tekrar taranır.\n"
            f"• FIDS Havalimanı Tablosu: <b>40 Saniyede</b> bir tazelenir."
        )
        send_telegram(msg, target_chat_id=chat_id)
        return

    elif raw_text.startswith("📍 Kapım") or raw_text.startswith("📍 Konum") or raw_text in ["🔄 Yenile", "/yenile", "/tara"]:
        curr = CONFIG.get("user_gate", "F3")
        execute_proximity_radar(user_gate=curr, chat_id=chat_id)
        return

    elif raw_text in ["🛫 Gidişler", "/gidisler", "/gidis", "gidişler", "gidisler", "gidiş", "gidis", "kalkış", "kalkis"]:
        execute_departures_radar(chat_id=chat_id)
        return

    intent, arg1, arg2 = parse_user_intent(raw_text)

    if intent == "FLIGHT_GATE":
        set_manual_gate(arg1, arg2)
        cached_flights["time"] = 0
        send_telegram(f"✅ <b>{arg1}</b> kapısı <b>{arg2}</b> olarak kaydedildi.", target_chat_id=chat_id)
        execute_radar(target_flight=arg1, chat_id=chat_id)
        return

    elif intent == "SINGLE_FLIGHT":
        now_ist, arrs, deps, _ = fetch_iga_direct_flights()
        clean_target = normalize_flight(arg1)
        existing_gate = manual_gate(clean_target)
        target = next((f for f in arrs + deps if normalize_flight(f["flight_no"]) == clean_target), None)
        if not existing_gate and target and target.get("source_gate"):
            existing_gate = target["source_gate"]

        if existing_gate:
            execute_radar(target_flight=clean_target, custom_gate=existing_gate, chat_id=chat_id)
            return

        send_telegram(f"⏳ <b>{clean_target}</b> için iGA WhatsApp yanıtı bekleniyor...", target_chat_id=chat_id)
        direction = "departure" if target and "dep_time" in target else "arrival"
        flight_time = (target.get("arr_time") or target.get("dep_time")) if target else now_ist
        result = bridge_client.lookup(clean_target, date=flight_time.date().isoformat(), direction=direction)
        wa_gate = result.get("gate") if result.get("success") else None
        if wa_gate:
            set_manual_gate(clean_target, wa_gate)
        else:
            send_telegram("⚠️ WhatsApp'tan tam kapı doğrulanamadı. " + html.escape(result.get("error") or result.get("status", "")), target_chat_id=chat_id)
        execute_radar(target_flight=clean_target, custom_gate=wa_gate, chat_id=chat_id)
        return

    elif intent == "PROXIMITY":
        execute_proximity_radar(user_gate=arg1, chat_id=chat_id)
        return

    elif intent == "ARRIVALS":
        curr = CONFIG.get("user_gate", "F3")
        send_telegram(
            f"💡 Genel radar yerine kapı ve uçuş bazlı çalışıyoruz.\n"
            f"• Kapındaki uçuşlar için doğrudan kapını yaz: <code>{curr}</code> veya <code>A11</code>\n"
            f"• Belirli bir uçuş için uçuş kodunu yaz: <code>TK0630</code>",
            target_chat_id=chat_id
        )
        return

    elif intent == "DEPARTURES":
        execute_departures_radar(chat_id=chat_id)
        return

    elif intent == "DEPARTURES_PIER":
        execute_departures_radar(pier=arg1, chat_id=chat_id)
        return

    else:
        ai_reply = ask_gpt4o_mini(raw_text, chat_id=chat_id)
        send_telegram(ai_reply, target_chat_id=chat_id)
        return


def handle_telegram_updates():
    global last_update_id
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 3}
    encoded = urllib.parse.urlencode(params)

    try:
        req = urllib.request.Request(f"{url}?{encoded}")
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if not data.get("ok"):
            return

        for update in data.get("result", []):
            last_update_id = update["update_id"]
            msg = update.get("message")
            if not msg:
                cb = update.get("callback_query")
                if cb:
                    msg = cb.get("message", {})
                    msg["text"] = cb.get("data", "")
                    msg["chat"] = cb.get("message", {}).get("chat", {})
            if not msg or not msg.get("text"):
                continue
            if telegram_slots.acquire(blocking=False):
                telegram_worker.submit(process_telegram_message, msg)
            else:
                send_telegram("⏳ Sorgu sırası dolu. Devam eden sorgular tamamlanınca yeniden yazabilirsin.",
                              target_chat_id=str(msg.get("chat", {}).get("id", TELEGRAM_CHAT_ID)))

    except Exception as e:
        print(f"[!] Telegram update döngü hatası: {e}", flush=True)

def run_bot_loop():
    print("[*] WCHS-IST Telegram Botu başlatıldı...", flush=True)
    start_background_crawler()
    while True:
        try:
            handle_telegram_updates()
        except Exception as err:
            print(f"[!] Beklenmeyen hata: {err}", flush=True)
            time.sleep(2)
        time.sleep(1)

if __name__ == "__main__":
    run_bot_loop()
