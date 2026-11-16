# 北海道降雪提醒 ❄️

盯住**小樽**和**函館**的降雪，一旦开始下雪就推到 iPhone（Bark），附带雪量、气温、体感、积雪深度和未来三小时。

## 工作原理

```
GitHub Actions（每 30 分钟）
        ↓
Open-Meteo 免费天气 API（无需 Key）
        ↓
判断：刚开始下雪 / 即将下雪 / 雪停
        ↓
Bark 推送 → iPhone 通知
```

数据源：[Open-Meteo](https://open-meteo.com/)。主源不可用时自动降级到 wttr.in 纯文本接口。

## 推送规则

| 情况 | 是否推送 |
|---|---|
| 上一小时没雪、这一小时有雪 | ❄️ 开始下雪（立即） |
| 现在没雪、下一小时要下 | 🌨 即将下雪（每小时只推一次） |
| 上一小时有雪、现在没了 | 🌤 雪停了 |
| 一直在下 | 只在 09:00 和 21:00 各推一次简报 |
| 一直没雪 | 不打扰 |

## 怎么自建

1. Fork 这个仓库
2. 改 `snow_alert.py` 开头的 `CITIES`，想加城市就加一行：

```python
CITIES = [
    {"name": "小樽", "en": "Otaru",    "lat": 43.1894, "lon": 141.0022},
    {"name": "函館", "en": "Hakodate", "lat": 41.7758, "lon": 140.7367},
]
```

3. 装 [Bark](https://apps.apple.com/app/bark-customed-notifications/id1403753865)，复制自己的 Device Key
4. 仓库 Settings → Secrets and variables → Actions，新建 `BARK_KEY`
5. 在 Actions 页面点「Run workflow」，勾上 `force` 测试一次

## 字段说明

| 字段 | 含义 | 单位 |
|---|---|---|
| `current.snowfall` | 当前小时降雪 | cm |
| `current.temperature_2m` | 气温 | °C |
| `current.apparent_temperature` | 体感温度 | °C |
| `current.snow_depth` | 积雪深度 | **米**（展示时 ×100） |
| `hourly.snowfall` | 逐小时降雪（`past_hours=1` 时第 1 项是上一小时） | cm |
| `daily.snowfall_sum` | 今日累计降雪 | cm |

## 本地测试

```bash
python3 snow_alert.py --selftest   # 用假数据跑全部分支，不发请求
BARK_KEY=xxx FORCE_PUSH=1 python3 snow_alert.py   # 真推一条
```

## 说明

纯 Python 标准库，无第三方依赖。山顶和海边雪量差异很大，当前坐标取的是市区中心。
