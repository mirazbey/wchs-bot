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
    "FRA", "MUC", "BER", "CDG", "AMS", "LHR", "LGW", "MAN", "BHX", "EDI",
    "VIE", "ZRH", "GVA", "FCO", "MXP", "BLQ", "VCE", "NAP", "MAD", "BCN", 
    "BRU", "DUS", "HAM", "STR", "PRG", "WAW", "BUD", "CPH", "ARN", "OSL", 
    "HEL", "LIS", "ATH", "DUB", "LYS", "NCE", "MRS", "HAJ", "CGN", "NUE",
    "JFK", "EWR", "ORD", "LAX", "MIA", "SFO", "BOS", "IAD", "IAH", "DFW", 
    "ATL", "SEA", "DTW", "PHL", "DEN", "YYZ", "YUL", "YVR"
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
bridge_client = GateBridgeClient(WA_BRIDGE_URL)
telegram_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="TelegramQueries")
telegram_slots = threading.BoundedSemaphore(6)


def set_manual_gate(flight, gate):
    flight = normalize_flight(flight)
    custom_flight_gates[flight] = exact_gate(gate)
    custom_gate_times[flight] = time.monotonic()


def manual_gate(flight):
    flight = normalize_flight(flight)
    if time.monotonic() - custom_gate_times.get(flight, -1e9) < 120:
        return custom_flight_gates.get(flight)
    custom_flight_gates.pop(flight, None)
    custom_gate_times.pop(flight, None)
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
        data_arr = _post(nature=0, page_size=40)
        raw_arr = data_arr.get("result", {}).get("data", {}).get("flights", [])
        for item in raw_arr:
            origin_iata = str(item.get("fromCityCode") or "").strip().upper()
            arr_dt = parse_iso_dt(item.get("estimatedDatetime")) or parse_iso_dt(item.get("scheduledDatetime"))
            if arr_dt:
                flight_no = normalize_flight(item.get("flightNumber", "TK"))
                dedup_key = f"{origin_iata}_{arr_dt.strftime('%H%M')}_{flight_no}"
                if dedup_key not in seen_arr:
                    seen_arr.add(dedup_key)
                    gate_raw = str(item.get("gate") or "").strip()
                    carousel = str(item.get("carousel") or "").strip()

                    gate_val = manual_gate(flight_no) or exact_gate(gate_raw) or UNKNOWN_GATE

                    arrivals.append({
                        "flight_no": flight_no,
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
        data_dep = _post(nature=1, page_size=40)
        raw_dep = data_dep.get("result", {}).get("data", {}).get("flights", [])
        last_date = raw_dep[-1].get("scheduledDatetime") if raw_dep else ""
        if last_date:
            try:
                data_dep2 = _post(nature=1, page_size=40, start_date=last_date, button="moreFlight")
                raw_dep.extend(data_dep2.get("result", {}).get("data", {}).get("flights", []))
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
                dedup_key = f"{dest_iata}_{dep_dt.strftime('%H%M')}_{flight_no}"
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
            [{"text": "🛬 Şu Anki İnişler"}, {"text": f"📍 Konum ({curr})"}],
            [{"text": "🚨 Acil Aktarmalar (<60 dk)"}, {"text": "🚪 İskele Seç"}],
            [{"text": "🔄 Yenile"}]
        ],
        "resize_keyboard": True
    }

def get_pier_keyboard():
    return {
        "keyboard": [
            [{"text": "📍 İskele A"}, {"text": "📍 İskele B"}, {"text": "📍 İskele D"}],
            [{"text": "📍 İskele E"}, {"text": "📍 İskele F"}, {"text": "🌐 Tüm İskeleler"}],
            [{"text": "🔙 Ana Menü"}]
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
    targets = gate_targets(user_gate)
    now_ist = get_now_ist()
    parts = [f"📍 <b>{' / '.join(targets)}</b> | 🕒 {now_ist:%H:%M}"]
    for gate in targets:
        lines = [f"🚪 <b>{gate}</b>"]
        for label, flights, time_key, city_key in [
            ("🛬 Geliş", arrivals, "arr_time", "origin_name"),
            ("🛫 Gidiş", departures, "dep_time", "dest")]:
            matches = [f for f in flights if exact_gate(f.get("gate")) == gate
                       and -40 <= (f[time_key] - now_ist).total_seconds() / 60 <= 100]
            matches.sort(key=lambda f: abs((f[time_key] - now_ist).total_seconds()))
            if not matches:
                lines.append(f"{label}: Doğrulanmış uçuş bilgisi yok.")
            for f in matches[:3]:
                lines.append(f"{label}: <b>{html.escape(f['flight_no'])}</b> · "
                             f"{html.escape(f.get(city_key, ''))} · {f[time_key]:%H:%M}")
        parts.append("\n".join(lines))
    if len(targets) == 2:
        # Base-only data has no evidence for the A/B side.
        base_matches = [f for f in arrivals + departures if exact_gate(f.get("gate")) == user_gate]
        if base_matches:
            parts.append(f"ℹ️ {user_gate}: " + ", ".join(html.escape(f['flight_no']) for f in base_matches[:3])
                         + " — A/B ayrımı kaynakta belirtilmemiş.")
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
    # Work on copies: WhatsApp results must not become permanent FIDS values.
    arrivals = [dict(f) for f in arrivals]
    departures = [dict(f) for f in departures]
    unknown = [f for f in arrivals if not exact_gate(f.get("gate"))
               and -40 <= (f["arr_time"] - now_ist).total_seconds() / 60 <= 60]
    unknown.sort(key=lambda f: abs((f["arr_time"] - now_ist).total_seconds()))
    limit = max(1, int(os.environ.get("WA_RADAR_MAX_QUERIES", "2")))
    candidates = unknown[:limit]
    note = ""
    if candidates:
        note = f"WhatsApp: {len(candidates)} yakın geliş sırayla doğrulanıyor. Her uçuşun yanıtı birkaç adımda gelebilir."
    message_id = send_telegram(render_gate_radar(user_gate, arrivals, departures, note), target_chat_id=chat_id)
    if not candidates:
        return
    health = bridge_client.status()
    if not health.get("connected") or health.get("protocol") != 2:
        note = "WhatsApp köprüsü bağlı değil veya güncel değil; geliş kapıları doğrulanamadı."
        update_telegram(render_gate_radar(user_gate, arrivals, departures, note), message_id, chat_id)
        return
    confirmed = 0
    failures = []
    for index, flight in enumerate(candidates, 1):
        def progress(state):
            descriptions = {"queued": "sırada", "waiting_reply": "ilk yanıt bekleniyor",
                "waiting_after_date": "tarih seçildi, kapı yanıtı bekleniyor",
                "searching": "iGA araştırıyor", "waiting_exact_gate": "tam kapı mesajı bekleniyor"}
            note = f"WhatsApp {index}/{len(candidates)}: {flight['flight_no']} — {descriptions.get(state, 'yanıt bekleniyor')}."
            update_telegram(render_gate_radar(user_gate, arrivals, departures, note), message_id, chat_id)
        result = bridge_client.lookup(flight["flight_no"], date=flight["arr_time"].date().isoformat(), progress=progress)
        if result.get("success"):
            flight["gate"] = result["gate"]
            confirmed += 1
        else:
            failures.append(result.get("status"))
        note = f"WhatsApp: {index}/{len(candidates)} sorgu tamamlandı; {confirmed} gelişin kapısı doğrulandı."
        update_telegram(render_gate_radar(user_gate, arrivals, departures, note), message_id, chat_id)
        if result.get("status") in {"disconnected", "unreachable", "consent_required"}:
            break
    remaining = len(unknown) - confirmed
    note = f"WhatsApp: {confirmed} gelişin kapısı doğrulandı."
    if remaining:
        note += f" {remaining} yakın gelişin kapısı hâlâ doğrulanamadı; liste eksik olabilir."
    if failures:
        reasons = {"timeout": "iGA yanıtı zaman aşımına uğradı", "partial": "tam kapı numarası gelmedi",
                   "consent_required": "iGA hesabında KVKK onayı gerekiyor", "unreachable": "köprüye ulaşılamadı",
                   "disconnected": "WhatsApp bağlantısı kesildi", "not_announced": "kapı açıklanmamış"}
        note += " " + reasons.get(failures[-1], "Bazı sorgularda tam kapı bilgisi alınamadı") + "."
    update_telegram(render_gate_radar(user_gate, arrivals, departures, note), message_id, chat_id)


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

    header = f"🛬 <b>ŞU ANKİ İNİŞLER & AKTARMALAR</b> | 🕒 <b>{now_ist.strftime('%H:%M')}</b>\n───────────────────────\n"

    oss_arrivals = [a for a in arrivals if a["is_oss"]]
    active_arrs = oss_arrivals if oss_arrivals else arrivals

    cards = []
    for arr in active_arrs:
        arr_flight = arr["flight_no"]
        origin = arr["origin_name"]
        arr_time = arr["arr_time"]
        arr_gate = custom_gate or arr["gate"]

        if CONFIG["selected_pier"] and not arr_gate.startswith(CONFIG["selected_pier"]):
            continue

        diff_now = int((arr_time - now_ist).total_seconds() / 60)
        status_text = f"İndi ({abs(diff_now)} dk önce)" if diff_now <= 0 else f"İniş: {arr_time.strftime('%H:%M')} ({diff_now} dk sonra)"

        best_conn = None
        for dep in departures:
            delta_arr = (dep["dep_time"] - arr_time).total_seconds() / 60
            rem_now_min = (dep["dep_time"] - now_ist).total_seconds() / 60

            walk_min, tag, risk_str, risk_level = calc_transfer_metrics(arr_gate, dep["gate"], rem_now_min)
            if 35 <= delta_arr <= 200 and rem_now_min >= (walk_min + 15):
                if best_conn is None or risk_level < best_conn[0] or (risk_level == best_conn[0] and rem_now_min < best_conn[1]):
                    best_conn = (risk_level, rem_now_min, dep, walk_min, tag, risk_str)

        if best_conn:
            item_str = (
                f"• <b>{arr_flight} ({origin})</b> ➔ KAPI: <b>{arr_gate}</b>\n"
                f"  {status_text} | 🇪🇺 OSS\n"
                f"  ↳ <b>Aktarma:</b> {best_conn[2]['flight_no']} ➔ {best_conn[2]['dest']} (Kapı: <b>{best_conn[2]['gate']}</b>)\n"
                f"     Kalkış: <b>{best_conn[2]['dep_time'].strftime('%H:%M')}</b> (<b>{int(best_conn[1])} dk</b>) | {best_conn[5]}"
            )
            cards.append(item_str)

    if cards:
        send_telegram(header + "\n\n".join(cards[:3]), target_chat_id=chat_id)
    else:
        send_telegram(header + "ℹ️ Şu anda bu kriterlere uyan aktif aktarma bulunamadı.", target_chat_id=chat_id)

def parse_user_intent(raw_text: str):
    t = raw_text.strip()
    t_clean = t.upper().replace("İ", "I")

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

    t_lower = t.lower()
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

    if raw_text in ["🔄 Yenile", "🔄 Ekranı Yenile", "/tara", "/simdi"]:
        CONFIG["filter_mode"] = "ALL"
        CONFIG["selected_pier"] = None
        cached_flights["time"] = 0
        execute_radar(chat_id=chat_id)
        return

    elif raw_text.startswith("📍 Konum"):
        curr = CONFIG.get("user_gate", "F3")
        execute_proximity_radar(user_gate=curr, chat_id=chat_id)
        return

    elif raw_text in ["🚨 Acil Aktarmalar (<60 dk)", "🚨 Acil / Kritik (<70 dk)", "/kritik"]:
        CONFIG["filter_mode"] = "CRITICAL"
        CONFIG["selected_pier"] = None
        cached_flights["time"] = 0
        send_telegram("🚨 <b>Acil Aktarmalar Filtrelendi:</b>", target_chat_id=chat_id)
        execute_radar(chat_id=chat_id)
        return

    elif raw_text in ["🚪 İskele Seç", "🚪 İskele Seç (A-B-D-E-F)", "/iskele"]:
        send_telegram(
            "📍 <b>Takip etmek istediğiniz iskeleyi seçin:</b>",
            reply_markup=get_pier_keyboard(),
            target_chat_id=chat_id
        )
        return

    elif raw_text.startswith("📍 İskele "):
        pier = raw_text.replace("📍 İskele ", "").strip().upper()
        CONFIG["selected_pier"] = pier
        CONFIG["filter_mode"] = "ALL"
        cached_flights["time"] = 0
        send_telegram(f"🎯 <b>{pier} İskelesi Filtrelendi.</b>", reply_markup=get_main_keyboard(), target_chat_id=chat_id)
        execute_radar(chat_id=chat_id)
        return

    elif raw_text in ["🌐 Tüm İskeleler", "🔙 Ana Menü"]:
        CONFIG["selected_pier"] = None
        CONFIG["filter_mode"] = "ALL"
        cached_flights["time"] = 0
        send_telegram("🌐 <b>Tüm İskeleler Aktif.</b>", reply_markup=get_main_keyboard(), target_chat_id=chat_id)
        execute_radar(chat_id=chat_id)
        return

    intent, arg1, arg2 = parse_user_intent(raw_text)

    if intent == "FLIGHT_GATE":
        set_manual_gate(arg1, arg2)
        cached_flights["time"] = 0
        send_telegram(f"✅ <b>{arg1}</b> kapısı <b>{arg2}</b> olarak kaydedildi.", target_chat_id=chat_id)
        execute_radar(target_flight=arg1, chat_id=chat_id)
        return

    elif intent == "SINGLE_FLIGHT":
        send_telegram(f"⏳ <b>{arg1}</b> için iGA WhatsApp yanıtı bekleniyor; tarih seçimi sonrası mesajların tamamı alınacak.", target_chat_id=chat_id)
        now_ist, arrs, deps, _ = fetch_iga_direct_flights()
        target = next((f for f in arrs + deps if normalize_flight(f["flight_no"]) == arg1), None)
        direction = "departure" if target and "dep_time" in target else "arrival"
        flight_time = (target.get("arr_time") or target.get("dep_time")) if target else now_ist
        result = bridge_client.lookup(arg1, date=flight_time.date().isoformat(), direction=direction)
        wa_gate = result.get("gate") if result.get("success") else None
        if not wa_gate:
            send_telegram("⚠️ WhatsApp'tan tam kapı doğrulanamadı. " + html.escape(result.get("error") or result.get("status", "")), target_chat_id=chat_id)
        execute_radar(target_flight=arg1, custom_gate=wa_gate, chat_id=chat_id)
        return

    elif intent == "PROXIMITY":
        execute_proximity_radar(user_gate=arg1, chat_id=chat_id)
        return

    elif intent == "ARRIVALS":
        CONFIG["filter_mode"] = "ALL"
        CONFIG["selected_pier"] = None
        cached_flights["time"] = 0
        execute_radar(chat_id=chat_id)
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
            msg = update.get("message", {})
            if not msg.get("text"):
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
    while True:
        try:
            handle_telegram_updates()
        except Exception as err:
            print(f"[!] Beklenmeyen hata: {err}", flush=True)
            time.sleep(2)
        time.sleep(1)

if __name__ == "__main__":
    run_bot_loop()
