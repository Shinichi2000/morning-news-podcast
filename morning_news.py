import os
import datetime
import asyncio
import json
import re
import requests

import edge_tts
import feedparser
import yfinance as yf

# 定数
JST = datetime.timezone(datetime.timedelta(hours=9))
NOW_JST = datetime.datetime.now(JST)
WEEKDAYS_JP = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]
TODAY_JP = NOW_JST.strftime("%Y年%m月%d日") + WEEKDAYS_JP[NOW_JST.weekday()]
TODAY = NOW_JST.strftime("%Y-%m-%d")

OUTPUT_DIR = "docs"
OUTPUT_MP3 = os.path.join(OUTPUT_DIR, "podcast.mp3")
VOICE = "ja-JP-NanamiNeural"

# RSSフィード
RSS_FEEDS_WORLD = {
    "Reuters World": "https://feeds.reuters.com/reuters/topNews",
    "BBC World":     "http://feeds.bbci.co.uk/news/world/rss.xml",
}

RSS_FEEDS_JAPAN = {
    "NHK 主要":     "https://www3.nhk.or.jp/rss/news/cat0.xml",
    "NHK 社会":     "https://www3.nhk.or.jp/rss/news/cat1.xml",
    "NHK 経済":     "https://www3.nhk.or.jp/rss/news/cat3.xml",
    "NHK 政治":     "https://www3.nhk.or.jp/rss/news/cat4.xml",
}

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

STOCK_TICKERS = {
    "日経平均": "^N225",
    "S&P500":  "^GSPC",
    "ドル円":   "JPY=X",
}

def fetch_rss(feeds, max_per_feed=3):
    items = []
    for name, url in feeds.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                title = entry.get("title", "").strip()
                if title:
                    items.append(title)
        except Exception as e:
            print(f"  RSS取得エラー ({name}): {e}")
    return items


def fetch_stock_prices():
    result = {}
    for label, ticker in STOCK_TICKERS.items():
        try:
            data = yf.Ticker(ticker)
            hist = data.history(period="2d")
            if len(hist) >= 1:
                close = hist["Close"].iloc[-1]
                result[label] = round(close, 2)
        except Exception as e:
            print(f"  株価取得エラー ({label}): {e}")
    return result


def fetch_weather():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=33.3194&longitude=130.5081"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&timezone=Asia%2FTokyo&forecast_days=1"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        daily = data.get("daily", {})
        result = {
            "temp_max": daily.get("temperature_2m_max", [None])[0],
            "temp_min": daily.get("temperature_2m_min", [None])[0],
            "precip":   daily.get("precipitation_probability_max", [None])[0],
        }
        result["is_spring"] = NOW_JST.month in (3, 4, 5)
        return result
    except Exception as e:
        print(f"  天気取得エラー: {e}")
        return {}

def generate_script(world_news, japan_news, stock_data, weather):
    if not GEMINI_API_KEY:
        print("FAIL: GEMINI_API_KEY が設定されていません")
        return None

    world_text = "\n".join(f"- {t}" for t in world_news) if world_news else "（取得なし）"
    japan_text = "\n".join(f"- {t}" for t in japan_news) if japan_news else "（取得なし）"

    stock_lines = []
    for label, price in stock_data.items():
        if label == "ドル円":
            stock_lines.append(f"{label}: {price:.2f}円")
        elif label == "日経平均":
            stock_lines.append(f"{label}: {price:,.0f}円")
        else:
            stock_lines.append(f"{label}: {price:,.2f}")
    stock_text = "\n".join(stock_lines) if stock_lines else "（取得なし）"

    if weather:
        weather_text = (
            f"予想最高気温: {weather['temp_max']}°C、"
            f"予想最低気温: {weather['temp_min']}°C、"
            f"降水確率: {weather['precip']}%"
        )
        if weather.get("is_spring"):
            weather_text += "。春の季節なので、花粉と黄砂の飛散状況についても言及してください。"
    else:
        weather_text = "（取得なし）"

    prompt = f"""あなたはラジオパーソナリティです。
以下のデータをもとに、日本語のラジオ放送原稿を書いてください。

「厳守ルール」
- 冠頭は必ず『おはようございます。{{TODAY_JP}}、モーニングニュースです。』で始める
- キャスター名・担当者名は絶対に読み上げない（冠頭・末尾含め一切禁止）
- 交通情報のコーナーは設けない
- マークダウン記号（**、##、--- 等）は一切使わない
- 自然な話し言葉で書く

「放送構成（この順番で）」
1. 冠頭挨拶
2. 世界のニュース2〜3本
3. 日本の重要なニュース2〜3本
4. 株価コーナー（日経平均・S&P500・ドル円）
5. 福岡県久留米市の天気（最高気温・最低気温・降水確率、春は花粉と黄砂も）
6. 締めの挨拶（名前なし）

「世界のニュース見出し」
{world_text}

「日本のニュース見出し」
{japan_text}

「株価データ」
{stock_text}

「天気データ（福岡県久留米市）」
{weather_text}
"""

    import urllib.request
    headers = {"Content-Type": "application/json"}

    for model in GEMINI_MODELS:
        api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048},
        }).encode("utf-8")

        try:
            req = urllib.request.Request(api_url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                text = (
                    result.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
                if text:
                    print(f"  OK: {model} で原稿生成完了")
                    return text
        except Exception as e:
            print(f"  NG: {model} → {e}")

    return None

async def generate_audio(script, output_path):
    try:
        communicate = edge_tts.Communicate(script, VOICE)
        await communicate.save(output_path)
        print(f"  OK: 音声生成完了 → {output_path}")
        return True
    except Exception as e:
        print(f"  FAIL: 音声生成エラー → {e}")
        return False


def update_player_html():
    html_lines = [
        '<!DOCTYPE html>',
        '<html lang="ja">',
        '<head>',
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        '  <title>Morning News Podcast</title>',
        '  <style>',
        '    * { box-sizing: border-box; margin: 0; padding: 0; }',
        '    body {',
        '      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;',
        '      background: #f0f4f8;',
        '      display: flex; justify-content: center; align-items: center; min-height: 100vh;',
        '    }',
        '    .card {',
        '      background: #fff; border-radius: 16px;',
        '      box-shadow: 0 4px 20px rgba(0,0,0,.12);',
        '      padding: 36px 32px; max-width: 480px; width: 90%; text-align: center;',
        '    }',
        '    .icon { font-size: 48px; margin-bottom: 12px; }',
        '    h1 { font-size: 1.5rem; color: #1a202c; margin-bottom: 4px; }',
        '    .date { color: #718096; font-size: .9rem; margin-bottom: 24px; }',
        '    audio { width: 100%; margin-bottom: 20px; border-radius: 8px; }',
        '    .speed-btns { display: flex; justify-content: center; gap: 8px; flex-wrap: wrap; }',
        '    .speed-btns button {',
        '      padding: 6px 16px; border: 2px solid #4299e1; border-radius: 20px;',
        '      background: #fff; color: #4299e1; font-size: .85rem; font-weight: 600;',
        '      cursor: pointer; transition: background .2s, color .2s;',
        '    }',
        '    .speed-btns button.on { background: #4299e1; color: #fff; }',
        '    .speed-btns button:hover:not(.on) { background: #ebf8ff; }',
        '  </style>',
        '</head>',
        '<body>',
        '  <div class="card">',
        '    <div class="icon">🎙️</div>',
        '    <h1>Morning News</h1>',
        '    <p class="date" id="today-date"></p>',
        '    <audio id="player" controls src="podcast.mp3"></audio>',
        '    <div class="speed-btns">',
        '      <button onclick="setSpeed(1.0)"   id="s1"    class="on">1.0x</button>',
        '      <button onclick="setSpeed(1.25)"  id="s1_25"       >1.25x</button>',
        '      <button onclick="setSpeed(1.5)"   id="s1_5"        >1.5x</button>',
        '      <button onclick="setSpeed(2.0)"   id="s2"          >2.0x</button>',
        '    </div>',
        '  </div>',
        '  <script>',
        '    const player = document.getElementById("player");',
        '    const btnMap = { s1: 1.0, s1_25: 1.25, s1_5: 1.5, s2: 2.0 };',
        '    function setSpeed(rate) {',
        '      player.playbackRate = rate;',
        '      Object.entries(btnMap).forEach(([id, r]) => {',
        '        document.getElementById(id).classList.toggle("on", r === rate);',
        '      });',
        '    }',
        '    const d = new Date();',
        '    const days = ["日曜日","月曜日","火曜日","水曜日","木曜日","金曜日","土曜日"];',
        '    document.getElementById("today-date").textContent =',
        '      d.getFullYear() + "年" + (d.getMonth()+1) + "月" + d.getDate() + "日" + days[d.getDay()];',
        '<\/script>',
        '</body>',
        '</html>',
    ]
    html = '\n'.join(html_lines)
    index_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  OK: index.html 更新 → {index_path}")

def main():
    print("=" * 50)
    print("Morning News Generator - " + TODAY_JP)
    print("=" * 50)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("\n[1] ニュース取得中...")
    world_news = fetch_rss(RSS_FEEDS_WORLD)
    japan_news = fetch_rss(RSS_FEEDS_JAPAN)
    print(f"  世界: {len(world_news)}件 / 日本: {len(japan_news)}件")

    if not world_news and not japan_news:
        print("FAIL: ニュースが取得できませんでした")
        exit(1)

    print("\n[2] 株価取得中...")
    stock_data = fetch_stock_prices()
    print(f"  取得: {stock_data}")

    print("\n[3] 天気取得中（久留米市）...")
    weather = fetch_weather()
    print(f"  取得: {weather}")

    print("\n[4] 原稿生成中...")
    script = generate_script(world_news, japan_news, stock_data, weather)
    if not script:
        print("\nFAIL: 原稿生成に失敗しました")
        exit(1)

    script_path = os.path.join(OUTPUT_DIR, "script_" + TODAY + ".txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    print("  OK: 原稿保存完了")

    print("\n[5] 音声生成中...")
    success = asyncio.run(generate_audio(script, OUTPUT_MP3))
    if success:
        update_player_html()
        print("\nDONE!")
    else:
        exit(1)


if __name__ == "__main__":
    main()
