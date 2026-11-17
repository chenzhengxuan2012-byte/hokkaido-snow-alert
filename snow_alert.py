#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北海道降雪提醒（多城市版）
------------------------------------------------------------
覆盖城市：小樽、函館（可在 CITIES 里继续加札幌、二世谷等）
数据源  ：Open-Meteo（免费、无需 Key）；失败时先沿用近期旧读数，仍不可用才降级到 wttr.in
推送    ：Bark（App Store 免费 App，首页可复制自己的 Device Key）

判断逻辑（基于逐小时降雪量，past_hours=1 时数组形如
[上一小时, 当前小时, 下一小时, 后两小时 ...]）：
  上小时=0 且 本小时>0        -> 刚开始下雪，立即推送
  本小时=0 且 下小时>0        -> 即将下雪（每小时 :20~:39 只推一次，防刷屏）
  上小时>0 且 本小时=0        -> 雪停了，推一次
  持续降雪                    -> 09:00 / 21:00 各推一次简报

去重：每次推送都带「城市 + 事件 + 小时」签名，记录在 .snow_state.json，
     所以 job 内每 5 分钟循环一次、以及跨 job 都不会重复刷屏。

环境变量：
  BARK_KEY        必填，Bark Device Key
  BARK_SERVER     可选，默认 https://api.day.app
  FORCE_PUSH      可选，=1 时无视判断推一条测试通知
  SIMULATE_CITY   可选，填城市名模拟"刚开始下雪"
  FORCE_FALLBACK  可选，=1 时跳过主源直接用 wttr.in（测降级通知）
  LOOP_MINUTES    可选，>0 时在本 job 内循环检查这么久（默认 0，只跑一次）
  CHECK_INTERVAL  可选，循环间隔秒数，默认 300（5 分钟）

本地自测：
  python3 snow_alert.py --selftest
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Asia/Tokyo")
except Exception:
    LOCAL_TZ = timezone(timedelta(hours=9))

# ---------------------------------------------------------------- 城市配置
# 想加城市就在这里加一行：name=中文显示名，en=英文名（备用数据源用），lat/lon=坐标
CITIES = [
    {"name": "小樽", "en": "Otaru",    "lat": 43.1894, "lon": 141.0022},
    {"name": "函館", "en": "Hakodate", "lat": 41.7758, "lon": 140.7367},
]

BARK_KEY = os.getenv("BARK_KEY", "").strip()
BARK_SERVER = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")
FORCE_PUSH = os.getenv("FORCE_PUSH", "0").strip() == "1"
SIMULATE_CITY = os.getenv("SIMULATE_CITY", "").strip()   # 填城市名可模拟"刚开始下雪"
FORCE_FALLBACK = os.getenv("FORCE_FALLBACK", "0").strip() == "1"   # =1 时跳过主源，直接用备用源
LOOP_MINUTES = int(os.getenv("LOOP_MINUTES", "0").strip() or 0)    # >0 时在本 job 内循环检查
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300").strip() or 300)  # 循环间隔（秒）
STALE_MIN = 180                    # 主源失败时，多少分钟内的旧读数还值得沿用
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".snow_state.json")


# ---------------------------------------------------------------- 状态（去重 + 旧读数）
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception as exc:
        print("  状态写入失败：", repr(exc))


# ---------------------------------------------------------------- 工具函数
def fnum(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def snow_level(cm_per_hour):
    if cm_per_hour <= 0:
        return "无雪"
    if cm_per_hour < 0.5:
        return "小雪"
    if cm_per_hour < 1.5:
        return "中雪"
    return "大雪"


def http_json(url, retries=5, timeout=25):
    last = None
    for i in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "hokkaido-snow-alert/2.0 (+github-actions)"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            last = exc
            print(f"  第 {i} 次请求失败：{exc!r}")
            if i < retries:
                time.sleep(min(5 * i, 30))
    raise last


def http_text(url, timeout=20):
    req = urllib.request.Request(
        url, headers={"User-Agent": "hokkaido-snow-alert/2.0 (+github-actions)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8").strip()


def bark(title, body, group, level="timeSensitive"):
    if not BARK_KEY:
        print("  [未配置 BARK_KEY] 本应推送 ->", title)
        return True
    # 用 POST JSON，避免正文长/含换行时 URL 超长导致的 404
    payload = {
        "device_key": BARK_KEY,
        "title": title,
        "body": body,
        "group": group,
        "level": level,
        "icon": "https://raw.githubusercontent.com/chenzhengxuan2012-byte/hokkaido-snow-alert/main/assets/snowflake.png",
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BARK_SERVER}/push",
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print("  Bark:", r.status, r.read().decode("utf-8")[:80])
            return True
    except Exception as exc:
        print("  Bark 推送失败:", repr(exc))
        return False


def push_once(st, city, sig, title, body, group, level="timeSensitive"):
    """同一个「城市+事件+小时」只推一次；跨 job 靠 state 文件记忆。"""
    if st.get("pushed", {}).get(city) == sig:
        print(f"  已推送过（{sig}），跳过重复")
        return False
    if bark(title, body, group, level):
        st.setdefault("pushed", {})[city] = sig
        save_state(st)
        return True
    return False


# ---------------------------------------------------------------- 数据获取
def om_url(city):
    return (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={city['lat']}&longitude={city['lon']}"
        "&current=temperature_2m,apparent_temperature,snowfall,snow_depth,"
        "weather_code,wind_speed_10m,relative_humidity_2m"
        "&hourly=snowfall,temperature_2m"
        "&daily=snowfall_sum,temperature_2m_max,temperature_2m_min"
        "&past_hours=1&forecast_hours=6&forecast_days=1"
        "&timezone=Asia%2FTokyo"
    )


def fetch_primary(city):
    return http_json(om_url(city))


# ---------------------------------------------------------------- 判断与推送
def report(city, data, simulated=False, st=None):
    st = st if st is not None else {}
    cur = data["current"]
    hs = data["hourly"]["snowfall"]
    hours = data["hourly"]["time"]
    days = data["daily"]

    now_cm = fnum(hs[1])
    prev_cm = fnum(hs[0])
    nxt = [fnum(x) for x in hs[2:5]]
    temp = fnum(cur.get("temperature_2m"))
    feel = fnum(cur.get("apparent_temperature"))
    depth_cm = fnum(cur.get("snow_depth")) * 100     # 接口单位是米
    wind = fnum(cur.get("wind_speed_10m"))
    rh = fnum(cur.get("relative_humidity_2m"))
    today_cm = fnum(days["snowfall_sum"][0])
    tmax = fnum(days["temperature_2m_max"][0])
    tmin = fnum(days["temperature_2m_min"][0])

    local = datetime.now(LOCAL_TZ)
    next_hour = hours[2][11:16] if len(hours) > 2 else "--:--"

    detail = (
        f"雪量 {now_cm:.2f} cm/h（{snow_level(now_cm)}）\n"
        f"气温 {temp:.1f}°C（体感 {feel:.1f}°C）\n"
        f"今日 {tmin:.1f}~{tmax:.1f}°C｜积雪 {depth_cm:.0f} cm｜今日累计 {today_cm:.1f} cm\n"
        "未来3小时 " + " / ".join(f"{x:.2f}" for x in nxt) + " cm\n"
        f"风 {wind:.0f} km/h｜湿度 {rh:.0f}%"
    )
    if simulated:
        detail = "【模拟测试数据·非真实天气】\n" + detail
    tag = "（模拟）" if simulated else ""
    group = f"{city['name']}降雪"

    print(
        f"[{local:%m-%d %H:%M}] {city['name']}: 本小时 {now_cm:.2f} "
        f"上小时 {prev_cm:.2f} 未来 {nxt}"
    )

    if not simulated:
        # 记下这次成功读数，供主源抖动时兜底
        st.setdefault("last_good", {})[city["name"]] = {
            "hour": hours[1], "cm": now_cm, "temp": temp,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        save_state(st)

    if FORCE_PUSH:
        return push_once(
            st, city["name"], f"force:{local:%Y-%m-%dT%H:%M:%S}",
            f"🧪 {city['name']}降雪·测试推送{tag}", "通道正常。\n" + detail, group,
        )

    if prev_cm <= 0 < now_cm:
        return push_once(
            st, city["name"], f"start:{hours[1]}",
            f"❄️ {city['name']}开始下雪了{tag}", detail, group,
        )

    if prev_cm > 0 >= now_cm:
        return push_once(
            st, city["name"], f"stop:{hours[1]}",
            f"🌤 {city['name']}雪停了{tag}", detail, group,
        )

    if now_cm <= 0 < nxt[0] and 20 <= local.minute < 40:
        return push_once(
            st, city["name"], f"soon:{hours[2]}",
            f"🌨 {city['name']} {next_hour} 前后要下雪{tag}", detail, group,
        )

    if now_cm > 0 and local.hour in (9, 21) and local.minute < 30:
        return push_once(
            st, city["name"], f"brief:{local:%Y-%m-%d}:{local.hour}",
            f"❄️ {city['name']}降雪中·简报{tag}", detail, group,
        )

    return False


def report_fallback(city, text, st=None):
    """主源长时间不可用时的降级提示：wttr.in 的一行文字（强制公制）。"""
    st = st if st is not None else {}
    group = f"{city['name']}降雪"
    print(f"  {city['name']} 降级数据：{text}")
    if FORCE_PUSH or ("雪" in text and "无雪" not in text):
        local = datetime.now(LOCAL_TZ)
        return push_once(
            st, city["name"], f"fallback:{local:%Y-%m-%d}:{local.hour}",
            f"⚠️ {city['name']} · 主数据源异常",
            f"备用接口读数：{text}\n"
            "Open-Meteo 暂时连不上，本条只是系统提示，不代表真的在下雪——\n"
            "备用源用的是另一套模式数据，明显偏低可能误报（实测同刻主源 2.4°C、备用源报 -1°C）。\n"
            "下一次检查会自动重试主源。",
            group,
            level="passive",      # 安静通知：只进通知中心，不亮屏、不出声
        )
    return False


# ---------------------------------------------------------------- 模拟
def simulate(city_name):
    """造一份"刚开始下雪"的假数据，走完整的判断 + 推送链路。"""
    city = None
    for c in CITIES:
        if city_name in c["name"] or city_name.lower() in c["en"].lower():
            city = c
            break
    if city is None:
        print(f"没找到城市「{city_name}」，可选：{[c['name'] for c in CITIES]}")
        return

    now = datetime.now(LOCAL_TZ).replace(minute=0, second=0, microsecond=0)
    times = [(now + timedelta(hours=off)).strftime("%Y-%m-%dT%H:%M") for off in (-1, 0, 1, 2, 3, 4)]
    data = {
        "current": {
            "temperature_2m": -1.8, "apparent_temperature": -6.4,
            "snowfall": 0.80, "snow_depth": 0.08, "weather_code": 71,
            "wind_speed_10m": 22.0, "relative_humidity_2m": 88,
        },
        "hourly": {
            "time": times,
            "snowfall": [0.00, 0.80, 0.90, 1.20, 0.60, 0.30],   # 上一小时 0 -> 本小时 0.8 = 刚开始下
            "temperature_2m": [-1.5, -1.8, -2.0, -2.2, -1.9, -1.6],
        },
        "daily": {
            "time": [now.strftime("%Y-%m-%d")],
            "snowfall_sum": [2.4],
            "temperature_2m_max": [0.2],
            "temperature_2m_min": [-2.6],
        },
    }
    print(f"模拟：{city['name']} 刚开始下雪（0.80 cm/h）")
    report(city, data, simulated=True)


# ---------------------------------------------------------------- 主流程
def run_once():
    st = load_state()
    pushed = False
    for city in CITIES:
        print(f"— {city['name']} —")
        if FORCE_FALLBACK:
            print("  [强制备用源模式]")
            try:
                text = http_text(
                    f"https://wttr.in/{city['en']}?format=%C+%t+%p&m&lang=zh-cn"
                )
                pushed |= report_fallback(city, text, st)
            except Exception as exc:
                print(f"  备用源失败：{exc!r}")
            continue

        try:
            pushed |= report(city, fetch_primary(city), st=st)
            continue
        except Exception as exc:
            print(f"  主数据源失败：{exc!r}")

        # 主源抖动：如果最近一次成功读数还新鲜，就不惊动你，也不切备用源
        last = st.get("last_good", {}).get(city["name"])
        if last:
            try:
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(last["ts"])).total_seconds() / 60
            except Exception:
                age = 9e9
            if age <= STALE_MIN:
                print(f"  沿用 {age:.0f} 分钟前的读数（{last['hour'][11:16]} 雪量 {last['cm']:.2f} cm/h），本轮跳过")
                continue

        print("  旧读数已过期，切备用源")
        try:
            text = http_text(
                f"https://wttr.in/{city['en']}?format=%C+%t+%p&m&lang=zh-cn"
            )
            pushed |= report_fallback(city, text, st)
        except Exception as exc2:
            print(f"  备用源也失败：{exc2!r}")
    if not pushed:
        print("本轮无新增降雪事件，未推送。")


def run():
    if SIMULATE_CITY:
        simulate(SIMULATE_CITY)
        return
    if LOOP_MINUTES <= 0 or FORCE_PUSH or FORCE_FALLBACK:
        run_once()
        return
    end = time.time() + LOOP_MINUTES * 60
    n = 0
    while True:
        n += 1
        print(f"\n========== 第 {n} 次检查 ==========")
        try:
            run_once()
        except Exception as exc:
            print("  本轮异常：", repr(exc))
        if time.time() + CHECK_INTERVAL > end:
            print(f"循环结束（共 {n} 次检查）")
            break
        time.sleep(CHECK_INTERVAL)


def selftest():
    def pack(prev, now, n1, n2, n3):
        return {
            "current": {
                "temperature_2m": -1.2, "apparent_temperature": -5.8,
                "snowfall": now, "snow_depth": 0.12, "weather_code": 71,
                "wind_speed_10m": 18.0, "relative_humidity_2m": 88,
            },
            "hourly": {
                "time": [f"2026-11-20T{h:02d}:00" for h in (18, 19, 20, 21, 22, 23)],
                "snowfall": [prev, now, n1, n2, n3, n3],
            },
            "daily": {
                "time": ["2026-11-20"], "snowfall_sum": [3.1],
                "temperature_2m_max": [0.4], "temperature_2m_min": [-3.2],
            },
        }

    cases = [
        ("开始下雪", pack(0, 0.21, 0.21, 0.14, 0.21)),
        ("雪停", pack(0.21, 0, 0, 0, 0)),
        ("即将下雪", pack(0, 0, 0.21, 0.14, 0)),
        ("持续降雪", pack(0.21, 0.21, 0.21, 0.14, 0.21)),
    ]
    for name, data in cases:
        print("=" * 44, "\n场景:", name, "| 城市:", CITIES[0]["name"])
        report(CITIES[0], data)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        try:
            run()
        except Exception as exc:
            print("运行失败：", repr(exc))
            sys.exit(1)
