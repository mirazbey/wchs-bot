"""
WCHS-IST: İstanbul Havalimanı Otonom Transfer & Kapı Radarı
Tamamen "İSTEĞE BAĞLI (ON-DEMAND)" Mod:
- Arkada sessiz bekler, 5 dakikada bir otomatik mesaj ATMAZ.
- Yalnızca sen Telegram'dan butona bastığında veya kapı yazdığında İGA'yı sorgular ve kart basar.
"""

import json
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

TELEGRAM_BOT_TOKEN = "8629069082:AAHNKyakE_5GuZTdUg5lgyBA1n7JsQez-1o"
TELEGRAM_CHAT_ID = "968928191"

# One-Stop Security (Avrupa + ABD + Kanada Havalimanları)
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
    "only_international": True
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

def fetch_iga_direct_flights():
    global cached_flights
    now_ist = get_now_ist()

    # 60 saniyelik önbellek (kullanıcı arka arkaya butona basarsa sistemi yormasın)
    if time.time() - cached_flights["time"] < 60 and cached_flights["arrivals"]:
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
            if origin_iata in OSS_AIRPORTS:
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
                            "arr_time": arr_dt,
                            "gate": gate_val,
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
        print(f"[!] Gidiş hatası: {e}", flush=True)

    arrivals.sort(key=lambda x: x["arr_time"])
    departures.sort(key=lambda x: x["dep_time"])

    cached_flights = {"time": time.time(), "arrivals": arrivals, "departures": departures, "source": "iGA Canlı FIDS"}
    return now_ist, arrivals, departures, "iGA Canlı FIDS"

def calc_transfer_metrics(arr_gate: str, dep_gate: str, delta_min: float):
    arr_p = arr_gate[0].upper() if arr_gate else "?"
    dep_p = dep_gate[0].upper() if dep_gate else "?"

    arr_match = re.findall(r"\d+", arr_gate)
    dep_match = re.findall(r"\d+", dep_gate)
    arr_num = int(arr_match[0]) if arr_match else 5
    dep_num = int(dep_match[0]) if dep_match else 5

    # İskele Yürüme Süresi
    if (arr_p in {"E", "F"} and dep_p in {"A", "B"}) or (arr_p in {"A", "B"} and dep_p in {"E", "F"}):
        walk_min = 22
        tag = "🔴 UZAK BLOK"
    elif (arr_p in {"A", "B"} and dep_p in {"A", "B"}) or (arr_p in {"E", "F"} and dep_p in {"E", "F"}):
        if arr_p == dep_p:
            walk_min = 4 if abs(arr_num - dep_num) <= 3 else 8
            tag = "🟢 AYNI İSKELE"
        else:
            walk_min = 15
            tag = "🟡 ORTA / ÇAPRAZ"
    elif arr_p == "D" or dep_p == "D":
        walk_min = 10
        tag = "🟡 ORTA MESAFE"
    elif dep_p == "G":
        walk_min = 18
        tag = "🔵 İÇ HATLAR (G)"
    else:
        walk_min = 12
        tag = "🟡 STANDART"

    # Risk Hesabı
    net_margin = delta_min - walk_min - 20
    if net_margin <= 20:
        risk_str = f"🚨 ÇOK ACİL (Net Pay: {int(net_margin)} dk)"
        risk_level = 1
    elif net_margin <= 45:
        risk_str = f"⚠️ DİKKAT (Net Pay: {int(net_margin)} dk)"
        risk_level = 2
    else:
        risk_str = f"✅ RAHAT (Net Pay: {int(net_margin)} dk)"
        risk_level = 3

    return walk_min, tag, risk_str, risk_level

def get_main_keyboard():
    return {
        "keyboard": [
            [{"text": "🛬 Şu Anki İnişler"}, {"text": "🚨 Acil / Kritik (<70 dk)"}],
            [{"text": "🚪 İskele Seç (A-B-D-E-F)"}, {"text": "📋 Tüm Aktarmalar"}],
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

def send_telegram(text: str, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
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

def execute_radar(custom_gate=None, target_flight=None):
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

    if not arrivals or not departures:
        send_telegram(header + "⚠️ <b>Hata:</b> Uçuş verisi çekilemedi.")
        return

    cards = []
    for arr in arrivals:
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
            delta_min = (dep["dep_time"] - arr_time).total_seconds() / 60
            if 35 <= delta_min <= 180:
                walk_min, tag, risk_str, risk_level = calc_transfer_metrics(arr_gate, dep["gate"], delta_min)

                if CONFIG["filter_mode"] == "CRITICAL" and risk_level != 1:
                    continue

                connections.append({
                    "flight": dep["flight_no"],
                    "dest": dep["dest"],
                    "time": dep["dep_time"].strftime("%H:%M"),
                    "remaining": int(delta_min),
                    "gate": dep["gate"],
                    "walk_min": walk_min,
                    "tag": tag,
                    "risk_str": risk_str,
                    "risk_level": risk_level
                })

        connections.sort(key=lambda x: (x["risk_level"], x["remaining"]))

        if connections:
            card = (
                f"🛬 <b>GELİŞ: {arr_flight} | {origin}</b> ➔ KAPI: <code>{arr_gate}</code>\n"
                f"📊 <b>Durum:</b> {status_text} | {arr['status']}\n"
                f"─────────────────────────────────\n"
                f"📤 <b>BAĞLANTILI GİDİŞLER ({len(connections)} bağlantı):</b>\n"
            )
            for idx, c in enumerate(connections[:3], 1):
                card += (
                    f"<b>{idx}️⃣ 🛫 {c['flight']} ➔ {c['dest']} | KAPI: <code>{c['gate']}</code></b>\n"
                    f"   • Kalkış: <b>{c['time']}</b> (Kalan: <b>{c['remaining']} dk</b>)\n"
                    f"   • Mesafe: <code>{arr_gate}</code> ➔ <code>{c['gate']}</code> (~<b>{c['walk_min']} dk</b> | {c['tag']})\n"
                    f"   • Durum: {c['risk_str']}\n"
                )
            cards.append(card)

    if cards:
        full_msg = header + "\n═════════════════════════════════\n".join(cards[:4])
        send_telegram(full_msg)
    else:
        send_telegram(header + "ℹ️ Şu anda bu kriterlere uyan aktif OSS aktarması bulunamadı.")

def handle_telegram_updates():
    global last_update_id
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 2}
    encoded = urllib.parse.urlencode(params)
    
    try:
        req = urllib.request.Request(f"{url}?{encoded}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if not data.get("ok"):
            return

        for update in data.get("result", []):
            last_update_id = update["update_id"]
            raw_text = update.get("message", {}).get("text", "").strip()
            if not raw_text:
                continue

            # 1. Belirli bir uçuşa kapı atama (Örn: "TK1944 F4")
            m_flight_gate = re.match(r"^(TK\s*\d+)\s+([A-G]\d+[A-Z]?)$", raw_text.upper())
            if m_flight_gate:
                f_code = m_flight_gate.group(1).replace(" ", "")
                g_code = m_flight_gate.group(2)
                custom_flight_gates[f_code] = g_code
                cached_flights["time"] = 0
                send_telegram(f"✅ <b>{f_code}</b> uçuşunun geliş kapısı <b>{g_code}</b> olarak atandı! Rota hesaplanıyor...")
                execute_radar(target_flight=f_code)
                continue

            # 2. Genel kapı girişi (Örn: "F4", "F7", "E3", "A2")
            clean_text = raw_text.upper().replace(" ", "")
            if re.match(r"^[A-G]\d+[A-Z]?$", clean_text):
                gate_entered = clean_text
                send_telegram(f"🎯 <b>Kapı {gate_entered} için Anlık Aktarma Hesaplanıyor...</b>")
                execute_radar(custom_gate=gate_entered)

            # 3. Butonlara tıklandığında İSTEĞE BAĞLI çalışır
            elif raw_text in ["🛬 Şu Anki İnişler", "🔄 Ekranı Yenile", "/tara", "/simdi", "📋 Tüm Aktarmalar"]:
                CONFIG["filter_mode"] = "ALL"
                CONFIG["selected_pier"] = None
                cached_flights["time"] = 0
                execute_radar()

            elif raw_text in ["🚨 Acil / Kritik (<70 dk)", "/kritik"]:
                CONFIG["filter_mode"] = "CRITICAL"
                CONFIG["selected_pier"] = None
                cached_flights["time"] = 0
                send_telegram("🚨 <b>Yalnızca Acil / Yüksek Riskli Aktarmalar Filtrelendi!</b>")
                execute_radar()

            elif raw_text in ["🚪 İskele Seç (A-B-D-E-F)", "/iskele"]:
                send_telegram("📍 <b>Lütfen takip etmek istediğiniz iskeleyi seçin:</b>", reply_markup=get_pier_keyboard())

            elif raw_text.startswith("📍 İskele "):
                pier = raw_text.replace("📍 İskele ", "").strip().upper()
                CONFIG["selected_pier"] = pier
                CONFIG["filter_mode"] = "ALL"
                cached_flights["time"] = 0
                send_telegram(f"🎯 <b>{pier} İskelesi Filtrelendi.</b>", reply_markup=get_main_keyboard())
                execute_radar()

            elif raw_text in ["🌐 Tüm İskeleler", "🔙 Ana Menü"]:
                CONFIG["selected_pier"] = None
                CONFIG["filter_mode"] = "ALL"
                cached_flights["time"] = 0
                send_telegram("🌐 <b>Tüm İskeleler Aktif.</b>", reply_markup=get_main_keyboard())
                execute_radar()

    except Exception as e:
        print(f"[!] Telegram update hatası: {e}", flush=True)

# ==============================================================================
# SESSİZ DİNLEME MODU (OTOMATİK SPAM YOK - SADECE BUTONLA ÇALIŞIR)
# ==============================================================================
if __name__ == "__main__":
    print("[*] WCHS-IST Radar sessiz modda aktif (Sadece sen butona bastığında kart basacak)...", flush=True)
    
    # 7/24 arkada sessizce bekler, hiçbir otomatik mesaj ATMAZ.
    while True:
        handle_telegram_updates()
        time.sleep(1)
