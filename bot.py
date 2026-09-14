"""
WCHS-IST: İstanbul Havalimanı 7/24 Otonom Transfer & Kapı Radarı
- Konum Radarı: "ben f3deyim", "f3'teyim", "e4", "kapi d1" vb. doğal ifadelerle yakın kapı ve kalkış/iniş taraması.
- Aktarma Radarı: "şuanki inişler", "gelenler" ile canlı OSS aktarmaları.
- Akıllı Metin Ayrıştırıcı: "TK1716 D1" veya iletilen mesajlardan uçuş/kapı tespiti.
- Mesafe ve Emniyet Payı Matrisi: İskeleler arası yürüme dakikaları ve boarding risk hesaplaması.
"""

import json
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
    if not g1 or not g2 or any(k in g1 for k in ["Belirsiz", "Bölgesi"]) or any(k in g2 for k in ["Belirsiz", "Bölgesi"]):
        return 12, "🟡 STANDART (~12 dk)"

    p1 = g1[0].upper()
    p2 = g2[0].upper()
    n1_match = re.findall(r"\d+", g1)
    n2_match = re.findall(r"\d+", g2)
    n1 = int(n1_match[0]) if n1_match else 5
    n2 = int(n2_match[0]) if n2_match else 5

    if p1 == p2:
        diff = abs(n1 - n2)
        if diff == 0:
            return 0, "🎯 SENİN KAPIN (0 dk)"
        elif diff <= 2:
            return 1, f"🟢 ÇOK YAKIN ({diff} kapı | ~1 dk)"
        elif diff <= 5:
            return 3, f"🟡 AYNI İSKELE ({diff} kapı | ~3 dk)"
        else:
            return 6, f"🟡 AYNI İSKELE ({diff} kapı | ~6 dk)"

    pair = {p1, p2}
    if pair in [{"E", "F"}, {"A", "B"}]:
        return 7, "🟡 KOMŞU İSKELE (~7 dk)"
    elif "D" in pair:
        return 10, "🟡 MERKEZ BÖLGE (~10 dk)"
    elif (p1 in {"E", "F"} and p2 in {"A", "B"}) or (p1 in {"A", "B"} and p2 in {"E", "F"}):
        return 22, "🔴 UZAK BLOK (~22 dk)"
    elif "G" in pair:
        return 20, "🔵 İÇ HATLAR (~20 dk)"
    return 12, "🟡 STANDART (~12 dk)"

def calc_transfer_metrics(arr_gate: str, dep_gate: str, rem_now_min: float):
    walk_min, tag = calc_gate_dist(arr_gate, dep_gate)
    safety_margin = rem_now_min - walk_min - 20
    if safety_margin <= 15:
        risk_str = f"🚨 <b>ÇOK ACİL (Emniyet Payı: {int(safety_margin)} dk)</b>"
        risk_level = 1
    elif safety_margin <= 35:
        risk_str = f"⚠️ <b>DİKKAT (Emniyet Payı: {int(safety_margin)} dk)</b>"
        risk_level = 2
    else:
        risk_str = f"✅ <b>RAHAT (Emniyet Payı: {int(safety_margin)} dk)</b>"
        risk_level = 3
    return walk_min, tag, risk_str, risk_level

def fetch_iga_direct_flights():
    global cached_flights
    now_ist = get_now_ist()

    if time.time() - cached_flights["time"] < 45 and cached_flights["arrivals"]:
        return now_ist, cached_flights["arrivals"], cached_flights["departures"], cached_flights["source"]

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
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
                flight_no = str(item.get("flightNumber", "TK")).strip().upper()
                dedup_key = f"{origin_iata}_{arr_dt.strftime('%H%M')}_{flight_no}"
                if dedup_key not in seen_arr:
                    seen_arr.add(dedup_key)
                    gate_raw = str(item.get("gate") or "").strip()
                    carousel = str(item.get("carousel") or "").strip()

                    if flight_no in custom_flight_gates:
                        gate_val = custom_flight_gates[flight_no]
                    elif gate_raw and gate_raw not in ("", "-", "None"):
                        gate_val = gate_raw
                    else:
                        gate_val = f"F/E (Bant: {carousel})" if carousel else "F/E Bölgesi"

                    arrivals.append({
                        "flight_no": flight_no,
                        "origin_name": f"{item.get('fromCityName', '')} ({origin_iata})",
                        "origin_iata": origin_iata,
                        "is_oss": origin_iata in OSS_AIRPORTS,
                        "arr_time": arr_dt,
                        "gate": gate_val,
                        "carousel": carousel,
                        "status": item.get("remark") or "Planlandı"
                    })
    except Exception as e:
        print(f"[!] Geliş verisi hatası: {e}", flush=True)

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
            gate_val = gate if gate and gate not in ("", "-", "None") else "Belirsiz"

            if CONFIG["only_international"] and gate_val.startswith("G"):
                continue

            if dep_dt:
                flight_no = str(item.get("flightNumber", "TK")).strip().upper()
                dedup_key = f"{dest_iata}_{dep_dt.strftime('%H%M')}_{flight_no}"
                if dedup_key not in seen_dep:
                    seen_dep.add(dedup_key)
                    departures.append({
                        "flight_no": flight_no,
                        "dest": f"{item.get('toCityName', '')} ({dest_iata})",
                        "dest_iata": dest_iata,
                        "dep_time": dep_dt,
                        "gate": gate_val,
                        "counter": item.get("counter", ""),
                        "status": item.get("remark", "")
                    })
    except Exception as e:
        print(f"[!] Gidiş verisi hatası: {e}", flush=True)

    arrivals.sort(key=lambda x: x["arr_time"])
    departures.sort(key=lambda x: x["dep_time"])

    cached_flights = {"time": time.time(), "arrivals": arrivals, "departures": departures, "source": "iGA Canlı FIDS"}
    return now_ist, arrivals, departures, "iGA Canlı FIDS"

def get_main_keyboard():
    curr = CONFIG.get("user_gate", "F3")
    return {
        "keyboard": [
            [{"text": "🛬 Şu Anki İnişler"}, {"text": f"📍 Konum Radarı ({curr})"}],
            [{"text": "🚨 Acil / Kritik (<70 dk)"}, {"text": "🚪 İskele Seç (A-B-D-E-F)"}],
            [{"text": "🔄 Ekranı Yenile"}]
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
            pass
    except Exception as e:
        print(f"[!] Telegram gönderim hatası: {e}", flush=True)

def execute_proximity_radar(user_gate="F3", chat_id=None):
    user_gate = user_gate.upper().replace(" ", "")
    user_pier = user_gate[0]
    CONFIG["user_gate"] = user_gate
    now_ist, arrivals, departures, source = fetch_iga_direct_flights()

    lines = [
        f"📍 <b>KONUM RADARI: {user_gate} (İskele {user_pier})</b>",
        f"🕒 Canlı Saat: <b>{now_ist.strftime('%H:%M')}</b> (IST - {source})",
        "═════════════════════════════════"
    ]

    # 1. Nearby Departures
    dep_nearby = []
    for d in departures:
        g = d["gate"]
        if not g or g == "Belirsiz":
            continue
        walk_min, dist_tag = calc_gate_dist(user_gate, g)
        rem_min = int((d["dep_time"] - now_ist).total_seconds() / 60)
        if g.startswith(user_pier) and -5 <= rem_min <= 120:
            dep_nearby.append((walk_min, rem_min, d, dist_tag))

    dep_nearby.sort(key=lambda x: (x[0], x[1]))

    if dep_nearby:
        lines.append(f"🛫 <b>{user_pier} İSKELESİNDEKİ KALKIŞLAR ({len(dep_nearby)} uçuş):</b>")
        for walk_min, rem_min, d, dist_tag in dep_nearby[:4]:
            status_badge = f" [{d['status']}]" if d['status'] else ""
            rem_str = f"<b>{rem_min} dk</b> kaldı" if rem_min > 0 else "kalktı"
            lines.append(
                f"• 🛫 <b><code>{d['flight_no']}</code> ➔ {d['dest']}</b>\n"
                f"  🚪 Kapı: <b><code>{d['gate']}</code></b> ({dist_tag})\n"
                f"  ⏰ Kalkış: <b>{d['dep_time'].strftime('%H:%M')}</b> ({rem_str}){status_badge}"
            )
        lines.append("─────────────────────────────────")

    # 2. Nearby Arrivals
    arr_relevant = []
    for a in arrivals:
        g = a["gate"]
        diff_now = int((a["arr_time"] - now_ist).total_seconds() / 60)
        if -50 <= diff_now <= 60:
            if g.startswith(user_pier) or (user_pier in ["E", "F"] and "F/E" in g):
                arr_relevant.append((diff_now, a))

    arr_relevant.sort(key=lambda x: x[0])

    if arr_relevant:
        lines.append(f"🛬 <b>{user_pier} İSKELESİNE İNEN / YAKLAŞAN UÇAKLAR:</b>")
        for diff_now, a in arr_relevant[:3]:
            if diff_now <= 0:
                time_str = f"✅ İndi ({abs(diff_now)} dk önce - {a['arr_time'].strftime('%H:%M')})"
            else:
                time_str = f"⏳ Havada (~{diff_now} dk sonra - {a['arr_time'].strftime('%H:%M')})"

            oss_badge = "🇪🇺 OSS (Temiz)" if a["is_oss"] else "🌐 Genel Geliş"
            lines.append(
                f"• 🟤 <b><code>{a['flight_no']}</code></b> | 🟨 <b>{a['origin_name']}</b>\n"
                f"  🚪 Kapı/Bölge: <b><code>{a['gate']}</code></b> | {oss_badge}\n"
                f"  📊 {time_str}"
            )

            conns = []
            for dep in departures:
                delta_arr = (dep["dep_time"] - a["arr_time"]).total_seconds() / 60
                rem_now_min = (dep["dep_time"] - now_ist).total_seconds() / 60
                if 35 <= delta_arr <= 180 and rem_now_min > 5:
                    w_min, w_tag, risk_str, r_level = calc_transfer_metrics(user_gate, dep["gate"], rem_now_min)
                    conns.append((r_level, rem_now_min, dep, w_min, w_tag, risk_str))

            conns.sort(key=lambda x: (x[0], x[1]))
            if conns:
                top = conns[0]
                lines.append(
                    f"  ↳ 📤 <b>Bağlantılı Çıkış:</b> <code>{top[2]['flight_no']}</code> ➔ {top[2]['dest']} (Kapı: <code>{top[2]['gate']}</code>)\n"
                    f"     🚶 İntikal: <code>{user_gate}</code> ➔ <code>{top[2]['gate']}</code> (~<b>{top[3]} dk</b> | {top[4]})\n"
                    f"     {top[5]}"
                )
    else:
        lines.append(f"ℹ️ {user_pier} İskelesinde şu an aktif yeni iniş yok.")

    lines.append("═════════════════════════════════")
    lines.append("💡 <i>Kapı değiştirdiğinde <code>F7deyim</code>, <code>E2</code>, <code>D1teyim</code> yazman yeterli.</i>")
    send_telegram("\n".join(lines), target_chat_id=chat_id)

def execute_radar(custom_gate=None, target_flight=None, chat_id=None):
    now_ist, arrivals, departures, source = fetch_iga_direct_flights()

    filter_info = CONFIG["filter_mode"]
    if CONFIG["selected_pier"]:
        filter_info += f" | İskele: {CONFIG['selected_pier']}"
    if target_flight:
        filter_info += f" | Uçuş: {target_flight}"

    header = (
        f"🕒 <b>CANLI SAAT: {now_ist.strftime('%H:%M')} (IST - {source})</b>\n"
        f"🎯 <b>Filtre:</b> <code>{filter_info}</code>\n"
        "═════════════════════════════════\n"
    )

    oss_arrivals = [a for a in arrivals if a["is_oss"]]
    active_arrs = oss_arrivals if oss_arrivals else arrivals

    cards = []
    for arr in active_arrs:
        arr_flight = arr["flight_no"]
        origin = arr["origin_name"]
        arr_time = arr["arr_time"]
        arr_gate = custom_gate or arr["gate"]

        if target_flight and arr_flight != target_flight:
            continue

        if CONFIG["selected_pier"] and not arr_gate.startswith(CONFIG["selected_pier"]):
            continue

        diff_now = int((arr_time - now_ist).total_seconds() / 60)
        status_text = (
            f"🛬 <b>İndi ({abs(diff_now)} dk önce - {arr_time.strftime('%H:%M')})</b>"
            if diff_now <= 0 else
            f"✈️ <b>Havada (Tahmini: {arr_time.strftime('%H:%M')})</b>"
        )

        connections = []
        for dep in departures:
            delta_arr = (dep["dep_time"] - arr_time).total_seconds() / 60
            rem_now_min = (dep["dep_time"] - now_ist).total_seconds() / 60

            if 35 <= delta_arr <= 180 and rem_now_min > 5:
                walk_min, tag, risk_str, risk_level = calc_transfer_metrics(arr_gate, dep["gate"], rem_now_min)

                if CONFIG["filter_mode"] == "CRITICAL" and risk_level != 1:
                    continue

                connections.append({
                    "flight": dep["flight_no"],
                    "dest": dep["dest"],
                    "time": dep["dep_time"].strftime("%H:%M"),
                    "rem_now": int(rem_now_min),
                    "gate": dep["gate"],
                    "walk_min": walk_min,
                    "tag": tag,
                    "risk_str": risk_str,
                    "risk_level": risk_level
                })

        connections.sort(key=lambda x: (x["risk_level"], x["rem_now"]))

        if connections:
            card = (
                f"🟤 <b>[GELİŞ]</b> <code>{arr_flight}</code> | 🟨 <code>{origin}</code> ➔ KAPI: <b><code>{arr_gate}</code></b>\n"
                f"📊 <b>Durum:</b> {status_text} | {arr['status']}\n"
                f"─────────────────────────────────\n"
                f"📤 <b>BAĞLANTILI GİDİŞLER ({len(connections)} uçuş):</b>\n"
            )
            for idx, c in enumerate(connections[:3], 1):
                card += (
                    f"<b>{idx}️⃣ 🛫 <code>{c['flight']}</code> ➔ 🟨 <code>{c['dest']}</code> | KAPI: <code>{c['gate']}</code></b>\n"
                    f"   • Kalkış: <b>{c['time']}</b> (Kalan: <b>{c['rem_now']} dk</b>)\n"
                    f"   • İntikal: <code>{arr_gate}</code> ➔ <code>{c['gate']}</code> (~<b>{c['walk_min']} dk</b> | {c['tag']})\n"
                    f"   • {c['risk_str']}\n"
                )
            cards.append(card)

    if cards:
        full_msg = header + "\n═════════════════════════════════\n".join(cards[:4])
        send_telegram(full_msg, target_chat_id=chat_id)
    else:
        send_telegram(header + "ℹ️ Şu anda bu kriterlere uyan aktif OSS aktarması bulunamadı.", target_chat_id=chat_id)

def parse_user_intent(raw_text: str):
    t = raw_text.strip()
    t_clean = t.upper()

    if "TK" in t_clean:
        m_fg = re.search(r"(TK\s*\d+).*?\b([A-G])\s*(\d+[A-Z]?)\b", t_clean)
        if m_fg:
            flight = re.sub(r"\s+", "", m_fg.group(1))
            gate = f"{m_fg.group(2)}{m_fg.group(3)}"
            return "FLIGHT_GATE", flight, gate

        m_f = re.search(r"\b(TK\s*\d+)\b", t_clean)
        if m_f:
            flight = re.sub(r"\s+", "", m_f.group(1))
            return "SINGLE_FLIGHT", flight, None

    m_prox = re.search(
        r"\b([A-G])\s*[-_]?\s*(\d{1,2})([A-B])?(?:['’`]?\s*(?:DEYIM|DAYIM|TEYIM|TAYIM|DEYIZ|DAYIZ|TEYIZ|TAYIZ|DE|DA|TE|TA))?\b",
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

    return "UNKNOWN", None, None

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
            raw_text = msg.get("text", "").strip()
            chat_id = str(msg.get("chat", {}).get("id", TELEGRAM_CHAT_ID))

            if not raw_text:
                continue

            intent, arg1, arg2 = parse_user_intent(raw_text)

            if intent == "FLIGHT_GATE":
                custom_flight_gates[arg1] = arg2
                cached_flights["time"] = 0
                send_telegram(
                    f"🎯 <b>{arg1}</b> geliş kapısı <b>{arg2}</b> olarak sisteme kaydedildi! Rota hesaplanıyor...",
                    target_chat_id=chat_id
                )
                execute_radar(target_flight=arg1, chat_id=chat_id)
                continue

            elif intent == "SINGLE_FLIGHT":
                send_telegram(f"🔍 <b>{arg1}</b> için anlık kapı ve aktarma sorgulanıyor...", target_chat_id=chat_id)
                execute_radar(target_flight=arg1, chat_id=chat_id)
                continue

            elif intent == "PROXIMITY":
                execute_proximity_radar(user_gate=arg1, chat_id=chat_id)
                continue

            elif intent == "ARRIVALS":
                CONFIG["filter_mode"] = "ALL"
                CONFIG["selected_pier"] = None
                cached_flights["time"] = 0
                execute_radar(chat_id=chat_id)
                continue

            elif raw_text in ["🔄 Ekranı Yenile", "/tara", "/simdi", "📋 Tüm Aktarmalar"]:
                CONFIG["filter_mode"] = "ALL"
                CONFIG["selected_pier"] = None
                cached_flights["time"] = 0
                execute_radar(chat_id=chat_id)

            elif raw_text.startswith("📍 Konum Radarı"):
                curr = CONFIG.get("user_gate", "F3")
                execute_proximity_radar(user_gate=curr, chat_id=chat_id)

            elif raw_text in ["🚨 Acil / Kritik (<70 dk)", "/kritik"]:
                CONFIG["filter_mode"] = "CRITICAL"
                CONFIG["selected_pier"] = None
                cached_flights["time"] = 0
                send_telegram("🚨 <b>Yalnızca Acil / Yüksek Riskli Aktarmalar Filtrelendi!</b>", target_chat_id=chat_id)
                execute_radar(chat_id=chat_id)

            elif raw_text in ["🚪 İskele Seç (A-B-D-E-F)", "/iskele"]:
                send_telegram(
                    "📍 <b>Lütfen takip etmek istediğiniz iskeleyi seçin:</b>",
                    reply_markup=get_pier_keyboard(),
                    target_chat_id=chat_id
                )

            elif raw_text.startswith("📍 İskele "):
                pier = raw_text.replace("📍 İskele ", "").strip().upper()
                CONFIG["selected_pier"] = pier
                CONFIG["filter_mode"] = "ALL"
                cached_flights["time"] = 0
                send_telegram(
                    f"🎯 <b>{pier} İskelesi Filtrelendi.</b>",
                    reply_markup=get_main_keyboard(),
                    target_chat_id=chat_id
                )
                execute_radar(chat_id=chat_id)

            elif raw_text in ["🌐 Tüm İskeleler", "🔙 Ana Menü"]:
                CONFIG["selected_pier"] = None
                CONFIG["filter_mode"] = "ALL"
                cached_flights["time"] = 0
                send_telegram(
                    "🌐 <b>Tüm İskeleler Aktif.</b>",
                    reply_markup=get_main_keyboard(),
                    target_chat_id=chat_id
                )
                execute_radar(chat_id=chat_id)

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
