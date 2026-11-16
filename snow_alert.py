#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北海道降雪提醒（多城市版）
------------------------------------------------------------
覆盖城市：小樽、函館（可在 CITIES 里继续加札幌、二世谷等）
数据源  ：Open-Meteo（免费、无需 Key），失败时自动降级到 wttr.in 纯文本
推送    ：Bark（App Store 免费 App，首页可复制自己的 Device Key）

判断逻辑（基于逐小时降雪量，past_hours=1 时数组形如
[上一小时, 当前小时, 下一小时, 后两小时 ...]）：
  上小时=0 且 本小时>0        -> 刚开始下雪，立即推送
  本小时=0 且 下小时>0        -> 即将下雪（每小时 :20~:39 只推一次，防刷屏）
  上小时>0 且 本小时=0        -> 雪停了，推一次
  持续降雪                    -> 09:00 / 21:00 各推一次简报

环境变量：
  BARK_KEY     必填，Bark Device Key
  BARK_SERVER  可选，默认 https://api.day.app
  FORCE_PUSH   可选，=1 时无论有无雪都推一条测试通知

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


def http_json(url, retries=3, timeout=25):
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
                time.sleep(3 * i)
    raise last


def http_text(url, timeout=20):
    req = urllib.request.Request(
        url, headers={"User-Agent": "hokkaido-snow-alert/2.0 (+github-actions)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8").strip()


def bark(title, body, group):
    if not BARK_KEY:
        print("  [未配置 BARK_KEY] 本应推送 ->", title)
        return
    # 用 POST JSON，避免正文长/含换行时 URL 超长导致的 404
    payload = {
        "device_key": BARK_KEY,
        "title": title,
        "body": body,
        "group": group,
        "level": "timeSensitive",
        "icon": "https://cdn.jsdelivr.net/gh/Finb/bark@master/docs/icon.png",
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
    except Exception as exc:
        print("  Bark 推送失败:", repr(exc))


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
def report(city, data):
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
    group = f"{city['name']}降雪"

    print(
        f"[{local:%m-%d %H:%M}] {city['name']}: 本小时 {now_cm:.2f} "
        f"上小时 {prev_cm:.2f} 未来 {nxt}"
    )

    if FORCE_PUSH:
        bark(f"🧪 {city['name']}降雪·测试推送", "通道正常。\n" + detail, group)
        return True

    if prev_cm <= 0 < now_cm:
        bark(f"❄️ {city['name']}开始下雪了", detail, group)
        return True

    if prev_cm > 0 >= now_cm:
        bark(f"🌤 {city['name']}雪停了", detail, group)
        return True

    if now_cm <= 0 < nxt[0] and 20 <= local.minute < 40:
        bark(
            f"🌨 {city['name']} {next_hour} 前后要下雪",
            f"未来一小时预计 {nxt[0]:.2f} cm\n" + detail,
            group,
        )
        return True

    if now_cm > 0 and local.hour in (9, 21) and local.minute < 30:
        bark(f"❄️ {city['name']}降雪中·简报", detail, group)
        return True

    return False


def report_fallback(city, text):
    """Open-Meteo 挂掉时的降级：只看 wttr.in 的一行文字。"""
    group = f"{city['name']}降雪"
    print(f"  {city['name']} 降级数据：{text}")
    if FORCE_PUSH or ("雪" in text and "无雪" not in text):
        bark(
            f"❄️ {city['name']}（备用数据源）",
            f"当前：{text}\n主数据源暂时不可用，本条来自备用接口。",
            group,
        )
        return True
    return False


# ---------------------------------------------------------------- 主流程
def run():
    pushed = False
    for city in CITIES:
        print(f"— {city['name']} —")
        try:
            pushed |= report(city, fetch_primary(city))
        except Exception as exc:
            print(f"  主数据源失败：{exc!r}，切备用源")
            try:
                text = http_text(
                    f"https://wttr.in/{city['en']}?format=%C+%t+%p&lang=zh-cn"
                )
                pushed |= report_fallback(city, text)
            except Exception as exc2:
                print(f"  备用源也失败：{exc2!r}")
    if not pushed:
        print("本轮无新增降雪事件，未推送。")


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
