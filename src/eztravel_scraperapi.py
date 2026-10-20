import os
import sys
import json
import time
import random
import subprocess
from datetime import datetime

import requests

try:
    from config import ORIGIN, DESTINATION, DEPARTURE_DATE
except Exception:
    ORIGIN, DESTINATION, DEPARTURE_DATE = "TPE", "TYO", "2027-02-03"

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY", "")
SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"

# 內部 API 網址：請以 F12 看到的 Request URL 為準，必要時用環境變數覆蓋
API_URL = os.getenv("FLIGHT_API_URL", "https://flight.eztravel.com.tw/apiv2/flight/list")
TEMPLATE_PATH = os.getenv("FLIGHT_PAYLOAD_TEMPLATE", "data/flight_payload_template.json")

ORIGIN_CODE = os.getenv("FLIGHT_ORIGIN", ORIGIN)
DESTINATION_CODE = os.getenv("FLIGHT_DESTINATION", DESTINATION)
OUTBOUND_DATE = os.getenv("FLIGHT_DATE", DEPARTURE_DATE)
RETURN_DATE = os.getenv("FLIGHT_RETURN_DATE", "")

# 隨機延遲（秒）：每個請求之間、以及每一段航線之間
DELAY_RANGE = (float(os.getenv("DELAY_MIN", "4")), float(os.getenv("DELAY_MAX", "10")))
MAX_RETRIES = 3

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
POST_STEPS = ["transform.py", "load_mysql.py"]
os.makedirs("data/raw", exist_ok=True)

TOKENS = {"origin": "__ORIGIN__", "destination": "__DESTINATION__", "date": "__DATE__"}

# 網站搜尋用「城市碼」，config 裡有些是機場碼。送 payload 時換成城市碼。
# 可用環境變數 PAYLOAD_CODE_MAP='{"ICN":"SEL"}' 覆蓋。
PAYLOAD_CODE_MAP = {"ICN": "SEL", "KIX": "OSA"}
PAYLOAD_CODE_MAP.update(json.loads(os.getenv("PAYLOAD_CODE_MAP", "{}")))


def to_payload_code(code):
    return PAYLOAD_CODE_MAP.get(code, code)


# payload 裡的日期格式（F12 看到 "outboundDate": "28/01/2027"，即 日/月/年）
PAYLOAD_DATE_FORMAT = os.getenv("PAYLOAD_DATE_FORMAT", "%d/%m/%Y")


def to_payload_date(date_str):
    """內部一律用 YYYY-MM-DD，送出 payload 時轉成網站要的格式。"""
    return datetime.strptime(date_str, "%Y-%m-%d").strftime(PAYLOAD_DATE_FORMAT)


def random_sleep(label=""):
    s = random.uniform(*DELAY_RANGE)
    print(f"隨機等待 {s:.1f}s {label}")
    time.sleep(s)


# ==============================
# 產生 payload 範本
# ==============================

def make_template(captured_file):
    """把從瀏覽器複製的 payload 中的出發地/目的地/日期換成佔位符。"""
    with open(captured_file, encoding="utf-8") as f:
        text = f.read()
    for value, token in (
        (to_payload_code(ORIGIN_CODE), TOKENS["origin"]),
        (to_payload_code(DESTINATION_CODE), TOKENS["destination"]),
        (to_payload_date(OUTBOUND_DATE), TOKENS["date"]),
    ):
        if value not in text:
            print(f"WARNING: 在 payload 裡找不到 {value}，請確認抓的是這條航線/日期的請求，"
                  f"或手動把它改成 {token}")
        text = text.replace(value, token)
    os.makedirs(os.path.dirname(TEMPLATE_PATH) or ".", exist_ok=True)
    with open(TEMPLATE_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("已存範本:", TEMPLATE_PATH)


def build_payload(origin, destination, date):
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        text = f.read()
    text = (
        text.replace(TOKENS["origin"], to_payload_code(origin))
        .replace(TOKENS["destination"], to_payload_code(destination))
        .replace(TOKENS["date"], to_payload_date(date))
    )
    return json.loads(text)


# ==============================
# 透過 ScraperAPI 呼叫內部 API
# ==============================

def has_matching_routing(data, origin, destination, date):
    """沿用原本 handle_response 的檢查：回應必須真的是這一段航線。
    出發/抵達代碼接受原本的代碼，也接受送 payload 用的城市碼。"""
    origins = {origin, to_payload_code(origin)}
    destinations = {destination, to_payload_code(destination)}
    for routing in (data.get("data") or {}).get("routings") or []:
        sectors = routing.get("sectors") or []
        if not sectors:
            continue
        first = sectors[0]
        if (
            (origins & {first.get("departureCityCode"), first.get("departureAirportCode")})
            and (destinations & {first.get("arrivalCityCode"), first.get("arrivalAirportCode")})
            and first.get("departureDate") == date
        ):
            return True
    return False


def debug_no_match(data, origin, destination, date):
    """回應不符時，存檔並印出摘要，方便判斷是 payload 問題還是當天無班次。"""
    os.makedirs("data/debug", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"data/debug/no_match_{origin}_{destination}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("已存除錯檔:", path)

    inner = data.get("data") if isinstance(data, dict) else None
    print("最上層欄位:", list(data.keys()) if isinstance(data, dict) else type(data))
    if isinstance(data, dict):
        for k in ("code", "status", "message", "msg", "error"):
            if k in data:
                print(f"  {k}:", data[k])
    routings = (inner or {}).get("routings") or []
    print("routings 數量:", len(routings))
    seen = set()
    for r in routings:
        sectors = r.get("sectors") or []
        if sectors:
            s = sectors[0]
            seen.add((
                s.get("departureCityCode"), s.get("departureAirportCode"),
                s.get("arrivalCityCode"), s.get("arrivalAirportCode"),
                s.get("departureDate"),
            ))
    for item in list(seen)[:5]:
        print("  回應裡的第一段 (出發城市, 出發機場, 抵達城市, 抵達機場, 日期):", item)
    print(f"  我們預期的: {origin} -> {destination}, {date}")


def fetch_leg(origin, destination, date):
    """回傳解析後的 JSON；失敗回傳 None。"""
    payload = build_payload(origin, destination, date)
    print(
        f"送出搜尋條件："
        f"fromCityCode={payload['data'].get('fromCityCode')}, "
        f"toCityCode={payload['data'].get('toCityCode')}, "
        f"outboundDate={payload['data'].get('outboundDate')}"
    )
    params = {
        "api_key": SCRAPERAPI_KEY,
        "url": API_URL,
        "render": "false",        # 內部 API 回 JSON，不需要渲染，最省點數
        "country_code": "tw",     # 用台灣 IP，行為最接近一般使用者
        "keep_headers": "true",   # 讓下面自訂的 headers 原樣轉送
	"premium": "true",
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://flight.eztravel.com.tw",
        "Referer": "https://flight.eztravel.com.tw/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                SCRAPERAPI_ENDPOINT, params=params, headers=headers,
                data=json.dumps(payload), timeout=70,
            )
            print(
                f"[{origin}->{destination} {date}] "
                f"attempt {attempt} status={resp.status_code}"
            )

            if resp.status_code != 200:
                print(
                    f"HTTP {resp.status_code} response: "
                    f"{resp.text[:1500]}"
                )

            if resp.status_code == 200:
                data = resp.json()

                if data.get("data") is None:
                    print(
                        f"API 回傳 data=null："
                        f"{origin}->{destination}, {date}; "
                        f"head={data.get('head')}"
                    )
                    debug_no_match(data, origin, destination, date)
                    return None

                if has_matching_routing(data, origin, destination, date):
                    return data

                print(
                    f"API 有資料，但航線比對失敗："
                    f"{origin}->{destination}, {date}"
                )
                debug_no_match(data, origin, destination, date)
                return None
        except (requests.RequestException, ValueError) as e:
            print("請求或 JSON 解析失敗:", e)

        if attempt < MAX_RETRIES:
            time.sleep(random.uniform(*DELAY_RANGE) * attempt)  # 退避 + 隨機
    return None


def save_raw(data, origin, destination):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = f"data/raw/flight_{origin}_{destination}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("Response saved:", path)
    return path


def run_post_steps(origin, destination, date, raw_file):
    env = os.environ.copy()
    env.update(
        FLIGHT_ORIGIN=origin,
        FLIGHT_DESTINATION=destination,
        FLIGHT_DATE=date,
        FLIGHT_RETURN_DATE=RETURN_DATE,
        FLIGHT_TRIP_OUTBOUND_DATE=OUTBOUND_DATE,
        FLIGHT_TRIP_ORIGIN=ORIGIN_CODE,
        FLIGHT_TRIP_DESTINATION=DESTINATION_CODE,
        FLIGHT_RAW_FILE=os.path.abspath(raw_file),
    )
    for script in POST_STEPS:
        path = os.path.join(SRC_DIR, script)
        print("\nRunning:", script)
        cmd = [sys.executable, path]
        if script == "transform.py":
            cmd.append(os.path.abspath(raw_file))
        if subprocess.run(cmd, env=env).returncode != 0:
            print("Step failed:", script)
            return False
    return True


def scrape_leg(origin, destination, date):
    data = fetch_leg(origin, destination, date)
    if data is None:
        return False
    raw_file = save_raw(data, origin, destination)
    return run_post_steps(origin, destination, date, raw_file)


# ==============================
# main
# ==============================

def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--make-template":
        make_template(sys.argv[2])
        return

    if not SCRAPERAPI_KEY:
        sys.exit("請先設定環境變數 SCRAPERAPI_KEY")
    if not os.path.exists(TEMPLATE_PATH):
        sys.exit(f"找不到 {TEMPLATE_PATH}，請先照檔案開頭說明用 --make-template 產生")

    print(f"ORIGIN={ORIGIN_CODE} DESTINATION={DESTINATION_CODE} "
          f"OUTBOUND={OUTBOUND_DATE} RETURN={RETURN_DATE or '(不搜回程)'}")

    failed = []

    if not scrape_leg(ORIGIN_CODE, DESTINATION_CODE, OUTBOUND_DATE):
        failed.append("outbound")

    if RETURN_DATE and "outbound" not in failed:
        random_sleep("(去程 -> 回程)")
        if not scrape_leg(DESTINATION_CODE, ORIGIN_CODE, RETURN_DATE):
            failed.append("return")

    if failed:
        print("\nFAILED:", failed)
        sys.exit(1)
    print("\nDONE")


if __name__ == "__main__":
    main()
