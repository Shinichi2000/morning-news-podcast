#!/usr/bin/env python3
"""
自動モーニングニュース Podcast 生成スクリプト v3
修正: google-genai SDK使用、gemini-2.5系モデル、リトライ強化
"""

import os
import asyncio
import datetime
import time
import feedparser
import yfinance as yf
import edge_tts
from google import genai

# ============================================================
# 設定
# ============================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

STOCKS = {
    "LMT":    "ロッキード・マーティン",
    "NVDA":   "エヌビディア",
    "1540.T": "純金上場信託",
    "1911.T": "住友林業",
    "3231.T": "野村不動産HD",
    "4063.T": "信越化学工業",
    "8961.T": "森トラスト総合リート",
}

RSS_FEEDS = {
    "NHK 主要": "https://www.nhk.or.jp/rss/news/cat0.xml",
    "NHK 経済": "https://www.nhk.or.jp/rss/news/cat5.xml",
    "Bloomberg": "https://feeds.bloomberg.com/markets/news.rss",
    "WSJ World": "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
}

STOCK_KEYWORDS = {
    "LMT": "Lockheed Martin",
    "NVDA": "NVIDIA",
    "1911.T": "住友林業",
    "4063.T": "信越化学",
}

TTS_VOICE = "ja-JP-NanamiNeural"
OUTPUT_DIR = "docs"
TODAY = datetime.datetime.now().strftime("%Y-%m-%d")
OUTPUT_MP3 = os.path.join(OUTPUT_DIR, "morning_news_" + TODAY + ".mp3")


# ============================================================
# 1. ニュース収集
# ============================================================

def fetch_rss_news():
    print("NEWS: collecting...")
    all_news = {}

    for name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            entries = [e.get("title", "") for e in feed.entries[:4]]
            if entries:
                all_news[name] = entries
                print("  OK " + name + ": " + str(len(entries)))
        except Exception as e:
            print("  FAIL " + name + ": " + str(e))

    for ticker, keyword in STOCK_KEYWORDS.items():
        try:
            encoded = keyword.replace(" ", "+")
            url = "https://news.google.com/rss/search?q=" + encoded + "&hl=ja&gl=JP&ceid=JP:ja"
            feed = feedparser.parse(url)
            entries = [e.get("title", "") for e in feed.entries[:2]]
            if entries:
                all_news["NEWS_" + keyword] = entries
                print("  OK NEWS_" + keyword + ": " + str(len(entries)))
        except Exception as e:
            print("  FAIL NEWS_" + keyword + ": " + str(e))

    return all_news


# ============================================================
# 2. 株価取得
# ============================================================

def fetch_stock_prices():
    print("\nSTOCK: collecting...")
    stock_data = []

    for ticker, name in STOCKS.items():
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5d")
            if len(hist) >= 1:
                current = hist["Close"].iloc[-1]
                change_pct = 0
                if len(hist) >= 2:
                    prev = hist["Close"].iloc[-2]
                    change_pct = ((current - prev) / prev) * 100

                currency = "JPY" if ".T" in ticker else "USD"
                stock_data.append({
                    "name": name,
                    "price": round(current, 2),
                    "change_pct": round(change_pct, 2),
                    "currency": currency,
                })
                print("  OK " + name + ": " + str(round(current, 2)))
        except Exception as e:
            print("  FAIL " + name + ": " + str(e))

    return stock_data


# ============================================================
# 3. 原稿生成（新 google-genai SDK + リトライ）
# ============================================================

def call_gemini(prompt):
    """新SDKでGemini APIを呼び出し（リトライ付き）"""
    client = genai.Client(api_key=GEMINI_API_KEY)

    # 新SDKで使えるモデル（優先順）
    # NOTE: gemini-1.5系は廃止済(404)、gemini-2.0系は2026年6月廃止予定
    models = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
    ]

    for model_name in models:
        for attempt in range(3):
            try:
                print("  TRY " + str(attempt + 1) + "/3 (" + model_name + ")")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                text = response.text
                print("  OK: " + str(len(text)) + " chars with " + model_name)
                return text
            except Exception as e:
                err = str(e)
                if "429" in err or "quota" in err.lower() or "resource" in err.lower():
                    wait = 30 * (attempt + 1)
                    print("  RATE LIMITED -> waiting " + str(wait) + "s...")
                    time.sleep(wait)
                elif "404" in err or "not found" in err.lower():
                    print("  MODEL NOT FOUND: " + model_name + " -> skip")
                    break
                elif "403" in err or "permission" in err.lower() or "forbidden" in err.lower():
                    print("  AUTH ERROR (403): API key may be invalid or expired")
                    return None
                else:
                    print("  ERROR: " + str(e))
                    if attempt == 2:
                        break
                    time.sleep(10)
        print("  NEXT MODEL...")

    return None


def generate_script(news_data, stock_data):
    print("\nSCRIPT: generating...")

    news_text = ""
    for source, titles in news_data.items():
        news_text += source + "\n"
        for t in titles:
            news_text += "- " + t + "\n"

    stock_text = "STOCKS:\n"
    for s in stock_data:
        d = "UP" if s["change_pct"] >= 0 else "DOWN"
        c = "円" if s["currency"] == "JPY" else "ドル"
        stock_text += "- " + s["name"] + ": " + str(s["price"]) + c
        stock_text += " (" + d + str(abs(s["change_pct"])) + "%)\n"

    today_jp = datetime.datetime.now().strftime("%Y年%m月%d日")
    prompt = "プロのニュースキャスターとして、朝の通勤用ニュース原稿を日本語で作成。\n"
    prompt += "日付: " + today_jp + "\n"
    prompt += "構成: 挨拶→主要ニュース3-4本→株価→注目トピック深掘り→締め\n"
    prompt += "自然な話し言葉で5分程度。原稿のみ出力。\n\n"
    prompt += news_text + "\n" + stock_text

    return call_gemini(prompt)


# ============================================================
# 4. 音声合成（Edge TTS）
# ============================================================

async def generate_audio(script, output_path):
    print("\nAUDIO: generating...")
    try:
        communicate = edge_tts.Communicate(script, TTS_VOICE, rate="+5%")
        await communicate.save(output_path)
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print("  OK: " + output_path + " (" + str(round(size_mb, 1)) + "MB)")
        return True
    except Exception as e:
        print("  FAIL: " + str(e))
        return False


# ============================================================
# 5. HTMLプレーヤー
# ============================================================

def update_player_html():
    lines = []
    lines.append('<!DOCTYPE html>')
    lines.append('<html lang="ja"><head>')
    lines.append('<meta charset="UTF-8">')
    lines.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    lines.append('<title>Morning News</title>')
    lines.append('<style>')
    lines.append('*{margin:0;padding:0;box-sizing:border-box}')
    lines.append('body{font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif;')
    lines.append('background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);')
    lines.append('color:#fff;min-height:100vh;display:flex;align-items:center;justify-content:center}')
    lines.append('.c{text-align:center;padding:2rem;max-width:480px;width:100%}')
    lines.append('h1{font-size:1.5rem;margin-bottom:.5rem}')
    lines.append('.d{color:#aaa;margin-bottom:2rem;font-size:.9rem}')
    lines.append('audio{width:100%;margin:1.5rem 0;border-radius:30px}')
    lines.append('.i{color:#888;font-size:.8rem;margin-top:1rem}')
    lines.append('</style></head><body>')
    lines.append('<div class="c">')
    lines.append('<h1>Morning News</h1>')
    lines.append('<p class="d">' + TODAY + '</p>')
    lines.append('<audio controls preload="auto">')
    lines.append('<source src="morning_news_' + TODAY + '.mp3" type="audio/mpeg">')
    lines.append('</audio>')
    lines.append('<p class="i">Updated daily at 6AM JST</p>')
    lines.append('</div></body></html>')

    with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("  OK: index.html updated")


# ============================================================
# メイン
# ============================================================

def main():
    print("=" * 50)
    print("Morning News Generator - " + TODAY)
    print("=" * 50)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    news_data = fetch_rss_news()
    if not news_data:
        print("FAIL: no news collected")
        exit(1)

    stock_data = fetch_stock_prices()

    script = generate_script(news_data, stock_data)
    if not script:
        print("\nFAIL: script generation failed on all models.")
        print("  -> Gemini API free quota may be exhausted.")
        print("  -> Wait a few minutes and re-run.")
        exit(1)

    script_path = os.path.join(OUTPUT_DIR, "script_" + TODAY + ".txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    print("  OK: script saved")

    success = asyncio.run(generate_audio(script, OUTPUT_MP3))
    if success:
        update_player_html()
        print("\nDONE!")
    else:
        exit(1)


if __name__ == "__main__":
    main()
