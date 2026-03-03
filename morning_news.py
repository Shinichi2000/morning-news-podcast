#!/usr/bin/env python3
"""
自動モーニングニュース Podcast 生成スクリプト
毎朝6時に実行 → ニュース収集 → 原稿生成 → 音声合成 → MP3保存
"""

import os
import json
import asyncio
import datetime
import feedparser
import yfinance as yf
import google.generativeai as genai
import edge_tts

# ============================================================
# 設定
# ============================================================

# Gemini APIキー（GitHub Secretsから取得）
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# 保有銘柄リスト（ティッカー: 表示名）
STOCKS = {
    "LMT":    "ロッキード・マーティン",
    "NVDA":   "エヌビディア",
    "1540.T": "純金上場信託",
    "1911.T": "住友林業",
    "3231.T": "野村不動産HD",
    "4063.T": "信越化学工業",
    "8961.T": "森トラスト総合リート",
}

# RSSフィード一覧
RSS_FEEDS = {
    "NHK 主要ニュース": "https://www.nhk.or.jp/rss/news/cat0.xml",
    "NHK 国際": "https://www.nhk.or.jp/rss/news/cat6.xml",
    "NHK 経済": "https://www.nhk.or.jp/rss/news/cat5.xml",
    "Bloomberg": "https://feeds.bloomberg.com/markets/news.rss",
    "WSJ World": "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
    "WSJ Markets": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
}

# 銘柄ごとのGoogle Newsフィード（自動生成）
def get_stock_news_feeds():
    feeds = {}
    keywords = {
        "LMT": "Lockheed Martin",
        "NVDA": "NVIDIA",
        "1540.T": "金価格",
        "1911.T": "住友林業",
        "3231.T": "野村不動産",
        "4063.T": "信越化学",
        "8961.T": "森トラスト",
    }
    for ticker, keyword in keywords.items():
        encoded = keyword.replace(" ", "+")
        feeds[f"📈 {keyword}"] = (
            f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
        )
    return feeds

# 音声設定
TTS_VOICE = "ja-JP-NanamiNeural"  # 日本語女性（高品質）
# 他の選択肢: "ja-JP-KeitaNeural"（男性）

# 出力先
OUTPUT_DIR = "output"
TODAY = datetime.datetime.now().strftime("%Y-%m-%d")
OUTPUT_MP3 = os.path.join(OUTPUT_DIR, f"morning_news_{TODAY}.mp3")


# ============================================================
# 1. ニュース収集（RSS）
# ============================================================

def fetch_rss_news():
    """RSSフィードからニュース見出しを収集"""
    print("📰 ニュース収集中...")
    all_news = {}

    # メインRSS
    for name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            entries = []
            for entry in feed.entries[:5]:  # 各フィード最大5件
                entries.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "")[:200],
                    "link": entry.get("link", ""),
                })
            if entries:
                all_news[name] = entries
                print(f"  ✅ {name}: {len(entries)}件")
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    # 銘柄別Google News
    for name, url in get_stock_news_feeds().items():
        try:
            feed = feedparser.parse(url)
            entries = []
            for entry in feed.entries[:3]:  # 銘柄ニュースは3件まで
                entries.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "")[:200],
                })
            if entries:
                all_news[name] = entries
                print(f"  ✅ {name}: {len(entries)}件")
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    return all_news


# ============================================================
# 2. 株価取得（yfinance）
# ============================================================

def fetch_stock_prices():
    """保有銘柄の株価・変動を取得"""
    print("\n💹 株価取得中...")
    stock_data = []

    for ticker, name in STOCKS.items():
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5d")
            if len(hist) >= 1:
                current = hist["Close"].iloc[-1]
                if len(hist) >= 2:
                    prev = hist["Close"].iloc[-2]
                    change = current - prev
                    change_pct = (change / prev) * 100
                else:
                    change = 0
                    change_pct = 0

                # 通貨の判定
                currency = "円" if ".T" in ticker else "ドル"

                stock_data.append({
                    "name": name,
                    "ticker": ticker,
                    "price": round(current, 2),
                    "change": round(change, 2),
                    "change_pct": round(change_pct, 2),
                    "currency": currency,
                })
                direction = "↑" if change >= 0 else "↓"
                print(f"  ✅ {name}: {current:.2f}{currency} ({direction}{abs(change_pct):.2f}%)")
        except Exception as e:
            print(f"  ❌ {name} ({ticker}): {e}")

    return stock_data


# ============================================================
# 3. 原稿生成（Gemini API）
# ============================================================

def generate_script(news_data, stock_data):
    """Gemini APIでポッドキャスト原稿を生成"""
    print("\n✍️  原稿生成中...")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.0-flash")

    # ニュースデータを整形
    news_text = ""
    for source, articles in news_data.items():
        news_text += f"\n【{source}】\n"
        for a in articles:
            news_text += f"- {a['title']}\n"
            if a.get("summary"):
                news_text += f"  {a['summary']}\n"

    # 株価データを整形
    stock_text = "\n【保有銘柄の株価】\n"
    for s in stock_data:
        direction = "上昇" if s["change"] >= 0 else "下落"
        stock_text += (
            f"- {s['name']}({s['ticker']}): "
            f"{s['price']}{s['currency']} "
            f"前日比{direction} {abs(s['change_pct'])}%\n"
        )

    # プロンプト
    today_display = datetime.datetime.now().strftime("%Y年%m月%d日")
    prompt = f"""あなたはプロのニュースキャスターです。
以下のニュース素材と株価データを元に、朝の通勤時に聴く5〜8分のニュース原稿を日本語で作成してください。

【ルール】
1. 挨拶から始め、日付を伝える（{today_display}）
2. まず世界と日本の主要ニュースを3〜5本、簡潔に伝える
3. 次に経済・マーケットニュースと保有株の株価を伝える
4. 注目キーワードを1つ選び、30秒程度で深掘り解説する
5. 最後に締めの挨拶をする
6. 自然な話し言葉で、聴きやすい原稿にする
7. 見出しだけでなく、なぜ重要かの一言解説を加える
8. 原稿のみ出力する（メタ情報や注釈は不要）

【ニュース素材】
{news_text}

【株価データ】
{stock_text}
"""

    try:
        response = model.generate_content(prompt)
        script = response.text
        print(f"  ✅ 原稿生成完了（{len(script)}文字）")
        return script
    except Exception as e:
        print(f"  ❌ 原稿生成エラー: {e}")
        return None


# ============================================================
# 4. 音声合成（Edge TTS - 完全無料・高品質）
# ============================================================

async def generate_audio(script, output_path):
    """Edge TTSで原稿を音声に変換"""
    print(f"\n🔊 音声合成中（{TTS_VOICE}）...")

    try:
        communicate = edge_tts.Communicate(script, TTS_VOICE, rate="+5%")
        await communicate.save(output_path)
        file_size = os.path.getsize(output_path) / (1024 * 1024)
        print(f"  ✅ 音声ファイル保存完了: {output_path} ({file_size:.1f}MB)")
        return True
    except Exception as e:
        print(f"  ❌ 音声合成エラー: {e}")
        return False


# ============================================================
# 5. HTMLプレーヤー更新
# ============================================================

def update_player_html():
    """最新の音声を再生できるHTMLプレーヤーを更新"""
    html_content = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>モーニングニュース Podcast</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;
            background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
            color: #fff;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .container {
            text-align: center;
            padding: 2rem;
            max-width: 480px;
            width: 100%;
        }
        h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
        .date { color: #aaa; margin-bottom: 2rem; font-size: 0.9rem; }
        audio {
            width: 100%;
            margin: 1.5rem 0;
            border-radius: 30px;
        }
        .info {
            color: #888;
            font-size: 0.8rem;
            margin-top: 1rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎙️ モーニングニュース</h1>
        <p class="date">""" + TODAY + """</p>
        <audio controls preload="auto">
            <source src="morning_news_""" + TODAY + """.mp3" type="audio/mpeg">
            お使いのブラウザは音声再生に対応していません。
        </audio>
        <p class="info">毎朝6時に自動更新されます</p>
    </div>
</body>
</html>"""

    html_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"  ✅ プレーヤーHTML更新: {html_path}")


# ============================================================
# メイン処理
# ============================================================

def main():
    print("=" * 50)
    print(f"🎙️  モーニングニュース生成 - {TODAY}")
    print("=" * 50)

    # 出力ディレクトリ作成
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. ニュース収集
    news_data = fetch_rss_news()
    if not news_data:
        print("⚠️  ニュースが取得できませんでした")
        return

    # 2. 株価取得
    stock_data = fetch_stock_prices()

    # 3. 原稿生成
    script = generate_script(news_data, stock_data)
    if not script:
        print("⚠️  原稿生成に失敗しました")
        return

    # 原稿をテキストファイルにも保存
    script_path = os.path.join(OUTPUT_DIR, f"script_{TODAY}.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    print(f"  📝 原稿保存: {script_path}")

    # 4. 音声合成
    success = asyncio.run(generate_audio(script, OUTPUT_MP3))

    if success:
        # 5. HTMLプレーヤー更新
        update_player_html()
        print("\n" + "=" * 50)
        print("✅ 完了！音声ファイルが生成されました")
        print(f"   📁 {OUTPUT_MP3}")
        print("=" * 50)
    else:
        print("\n❌ 音声生成に失敗しました")


if __name__ == "__main__":
    main()
