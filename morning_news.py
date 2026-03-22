import os
import datetime
from datetime import date
import asyncio
import time
import requests
from google import genai
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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

# 原稿の最低文字数（これを下回る場合は生成失敗と見なす）
MIN_SCRIPT_LENGTH = 1500

# ===== ポートフォリオ設定 =====
PORTFOLIO = {
    # ===== 国内株式・ETF =====
    "1540.T": {
        "name": "純金上場信託",
        "shares": 13,
        "cost": 22819,
        "stop_loss": 19400,
        "take_profit": None,   # 金はポートフォリオ比率25%超でリバランス
        "currency": "JPY",
        "category": "コモディティ（金）",
    },
    "1671.T": {
        "name": "WTI原油ETF",
        "shares": 1,
        "cost": 5191,
        "stop_loss": 4672,
        "take_profit": 6229,
        "currency": "JPY",
        "category": "コモディティ（原油）",
        "max_hold_date": "2026-05-31",
        "note": "停戦報道で即売却",
    },
    "3231.T": {
        "name": "野村不動産HD",
        "shares": 100,
        "cost": 1102,
        "stop_loss": 950,
        "take_profit": 1250,
        "currency": "JPY",
        "category": "国内不動産",
    },
    "4063.T": {
        "name": "信越化学工業",
        "shares": 24,
        "cost": 4379,
        "stop_loss": 5800,
        "take_profit": 7150,
        "currency": "JPY",
        "category": "国内素材・化学",
    },
    "513A.T": {
        "name": "GX防衛テック日本株ETF",
        "shares": 0,
        "cost": 0,
        "stop_loss_pct": -12,
        "take_profit_pct": 25,
        "currency": "JPY",
        "category": "国内防衛テック",
    },
    # ===== 米国株式 =====
    "LMT": {
        "name": "ロッキード・マーチン",
        "shares": 3,
        "cost": 452.90,
        "stop_loss": 580,
        "take_profit": 720,
        "currency": "USD",
        "category": "米国防衛",
    },
    "NVDA": {
        "name": "エヌビディア",
        "shares": 6,
        "cost": 180.95,
        "stop_loss": 155,
        "take_profit": 250,
        "currency": "USD",
        "category": "米国半導体・AI",
    },
}

# ===== 投資信託（yfinanceで取得不可のためニュース収集のみ）=====
FUNDS = {
    "SBI_V_ex_US": {
        "name": "SBI・V・先進国株式（除く米国）インデックスファンド",
        "category": "先進国株式（除く米国）",
        "news_keywords": ["先進国株式", "欧州株", "FTSE", "VEA"],
    },
}

STOCK_TICKERS = list(PORTFOLIO.keys())

STOCK_NEWS_KEYWORDS = {
    "1540.T": "金価格 ゴールド",
    "1671.T": "原油価格 WTI",
    "3231.T": "野村不動産",
    "4063.T": "信越化学",
    "513A.T": "防衛 三菱重工 川崎重工 IHI",
    "LMT": "Lockheed Martin",
    "NVDA": "NVIDIA",
    "SBI_V_ex_US": "欧州株 先進国株式",
}

# ===== マーケット指標 =====
MARKET_INDICES = {
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ",
    "^N225": "日経平均",
    "JPY=X": "ドル円",
    "CL=F": "WTI原油先物",
    "GC=F": "金先物",
}

# ===== RSSフィード =====
RSS_FEEDS = {
    # 国際ニュース
    "NHK 国際": "https://www.nhk.or.jp/rss/news/cat6.xml",
    "Bloomberg": "https://feeds.bloomberg.com/markets/news.rss",
    "WSJ World": "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
    "WSJ Markets": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
    "Reuters Business": "https://feeds.reuters.com/reuters/businessNews",
    # 国内ニュース
    "NHK 主要": "https://www.nhk.or.jp/rss/news/cat0.xml",
    "NHK 経済": "https://www.nhk.or.jp/rss/news/cat5.xml",
    "NHK 政治": "https://www.nhk.or.jp/rss/news/cat4.xml",
}

RSS_MAX_ENTRIES = 5

SCRIPT_PROMPT = """あなたはプロの経済ニュースキャスターです。
以下のニュース素材と保有ポートフォリオのデータを元に、朝の通勤時に聴く6〜10分のニュース原稿を日本語で作成してください。

【原稿の構成 — 必ずこの順番で構成すること】

■ オープニング（15秒）
- 日付（{today}）を伝え、簡潔に挨拶する。キャスター名の自己紹介は入れない
- 「今日のポイント」として、最も重要なニュース1本を1文で予告する

■ 第1パート：国際ニュース（2〜3分）
- 世界の政治・経済・安全保障の主要ニュースを3〜5本伝える
- 各ニュースについて「何が起きたか」だけでなく「なぜ重要か」「市場への影響は何か」を1〜2文で解説する
- 米国・イラン情勢、中東情勢、米中関係など地政学的な動きは必ず含める
- ホルムズ海峡・原油供給に関するニュースがあれば原油ETF（1671）との関連に触れる

■ 第2パート：国内ニュース（1〜2分）
- 日本の政治・経済・金融政策の主要ニュースを2〜3本伝える
- 日銀の政策動向、為替、日経平均の前日動向には必ず触れる
- 国内不動産市場・金利動向のニュースがあれば野村不動産HD（3231）との関連に触れる

■ 第3パート：マーケット概況（1分）
- 前日の米国市場（S&P500、NASDAQ）の終値と動向
- 日経平均の前日終値と動向
- 為替（ドル円）の水準
- 原油価格（WTI）と金価格の水準

■ 第4パート：保有ポートフォリオ報告（2〜3分）
{hold_limit_alerts}
- 以下のルールに従って報告する：
  1. アラート対象銘柄（損切りまたは利確ラインまで5%以内）は必ず読み上げ、注意喚起する
  2. 前日比の変動が大きい銘柄（±2%以上）は個別に報告する
  3. 関連ニュースがある銘柄は、ニュースと株価を紐づけて解説する
  4. その他の銘柄は「大きな動きなし」で簡潔にまとめてよい
  5. 各銘柄を報告する際は「取得単価からの損益率」と「損切り・利確ラインまでの距離」を必ず伝える
  6. 原油ETFに「保有期間上限3ヶ月」「停戦報道で即売却」の注記がある場合、中東ニュースがあれば必ず言及する
  7. SBI・V・先進国株式ファンドは株価取得ができないため、欧州株・先進国株式市場の動向として伝える
  8. 週末（金曜日）の原稿では全銘柄の週間サマリーを入れる

■ 第5パート：注目トピック深掘り（1〜2分）
- その日のニュースから1つテーマを選び、30〜60秒で深掘り解説する
- 保有銘柄に関連するテーマを優先する（例：半導体規制→NVDA、防衛予算→LMT・513A、原油動向→1671）
- テーマがない場合は、マクロ経済の注目指標や今週の重要イベントを解説する

■ クロージング（30秒）
- 今週中に発表が予定されている重要な経済指標・イベントがあれば、日付とともに知らせる
  （例：FOMC声明、雇用統計、CPI、GDP速報、日銀政策決定会合、主要企業決算など）
- 翌営業日に控えている重要イベントがあれば特に強調する
- 簡潔に締めの挨拶をする

【禁止事項】
- 天気・気温・花粉・服装などの生活情報は一切入れない
- 芸能・スポーツニュースは入れない
- 投資の推奨・助言と受け取れる表現（「買うべき」「売るべき」等）は使わない
- 「AIが生成しました」等のメタ情報は入れない

【話し方のルール】
- 自然な話し言葉で、聴きやすい原稿にする
- 数字を読み上げる際は「6,531円」→「6,531円」のようにそのまま読む
- パーセンテージは「プラス49.1パーセント」のように正負を明示する
- 銘柄名は日本語名を使う（例：「ロッキード・マーチン」「エヌビディア」）
- パート間の切り替えは自然なつなぎ言葉を使い、「第1パート」などの見出しは読まない
- 為替・株価・指数の数値を読み上げる際は「日本時間○日○時時点で」「前日のニューヨーク市場の終値で」「前日の東証終値で」など、必ずいつ時点の数値かを明示する
- 「皆さん」「リスナーの皆さん」等の複数人に呼びかける表現は使わない。一人が聴いている前提で話す
- 原稿のみ出力する（メタ情報・注釈・マークダウン記法は不要）

【ニュース素材】
{news_text}

【マーケットデータ】
{market_text}

【保有ポートフォリオ状況】
{portfolio_text}

【本日の日付】
{today}
"""


def fetch_rss(feeds, max_per_feed=RSS_MAX_ENTRIES):
    items = []
    for name, url in feeds.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                title = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()
                if title:
                    if summary:
                        items.append(f"{title}：{summary[:100]}")
                    else:
                        items.append(title)
        except Exception as e:
            print(f"  RSS取得エラー ({name}): {e}")
    return items


def fetch_stock_prices():
    """ポートフォリオ銘柄の株価を取得"""
    result = {}
    for ticker in STOCK_TICKERS:
        try:
            data = yf.Ticker(ticker)
            hist = data.history(period="2d")
            if len(hist) >= 1:
                close = hist["Close"].iloc[-1]
                result[ticker] = round(float(close), 2)
        except Exception as e:
            print(f"  株価取得エラー ({ticker}): {e}")
    return result


def fetch_market_indices():
    """マーケット指標を取得"""
    result = {}
    for ticker, label in MARKET_INDICES.items():
        try:
            data = yf.Ticker(ticker)
            hist = data.history(period="2d")
            if len(hist) >= 1:
                close = hist["Close"].iloc[-1]
                result[label] = round(float(close), 2)
        except Exception as e:
            print(f"  指標取得エラー ({label}): {e}")
    return result


def calculate_portfolio_status(portfolio, stock_data):
    """各銘柄の損益率と損切り・利確ラインまでの距離を計算"""
    status = []
    for ticker, info in portfolio.items():
        current_price = stock_data.get(ticker)
        if current_price is None or info["shares"] == 0:
            continue

        cost = info["cost"]
        pnl_pct = ((current_price - cost) / cost) * 100

        # 損切りラインまでの距離
        if info.get("stop_loss"):
            sl_distance = ((current_price - info["stop_loss"]) / current_price) * 100
        elif info.get("stop_loss_pct"):
            sl_distance = -info["stop_loss_pct"] + pnl_pct
        else:
            sl_distance = None

        # 利確ラインまでの距離
        if info.get("take_profit"):
            tp_distance = ((info["take_profit"] - current_price) / current_price) * 100
        elif info.get("take_profit_pct"):
            tp_distance = info["take_profit_pct"] - pnl_pct
        else:
            tp_distance = None

        # アラート判定
        alert = ""
        if sl_distance is not None and sl_distance < 5:
            alert = "損切りライン接近"
        if tp_distance is not None and tp_distance < 5:
            alert = "利確ライン接近"

        status.append({
            "name": info["name"],
            "ticker": ticker,
            "current": current_price,
            "cost": cost,
            "pnl_pct": round(pnl_pct, 1),
            "sl_distance": round(sl_distance, 1) if sl_distance is not None else None,
            "tp_distance": round(tp_distance, 1) if tp_distance is not None else None,
            "alert": alert,
            "currency": info["currency"],
            "category": info["category"],
        })
    return status


def format_market_text(market_data):
    lines = []
    for label, price in market_data.items():
        if label == "ドル円":
            lines.append(f"{label}: {price:.2f}円")
        elif label in ("WTI原油先物", "金先物"):
            lines.append(f"{label}: ${price:,.2f}")
        elif label in ("S&P 500", "NASDAQ"):
            lines.append(f"{label}: {price:,.2f}")
        else:
            lines.append(f"{label}: {price:,.2f}円")
    return "\n".join(lines) if lines else "（取得なし）"


def format_portfolio_text(portfolio_status):
    if not portfolio_status:
        lines = []
    else:
        lines = []
        for s in portfolio_status:
            line = (
                f"【{s['name']}（{s['ticker']}）】"
                f" 現在値: {s['current']:,}{s['currency']}"
                f" / 取得単価: {s['cost']:,}{s['currency']}"
                f" / 損益率: {'+' if s['pnl_pct'] >= 0 else ''}{s['pnl_pct']}%"
            )
            if s["sl_distance"] is not None:
                line += f" / 損切りまで: {s['sl_distance']:.1f}%"
            if s["tp_distance"] is not None:
                line += f" / 利確まで: {s['tp_distance']:.1f}%"
            if s["alert"]:
                line += f" ★{s['alert']}★"
            lines.append(line)

    # 未保有銘柄（shares=0）の情報も追記
    for ticker, info in PORTFOLIO.items():
        if info["shares"] == 0:
            extra = ""
            if info.get("note"):
                extra = f"（{info['note']}）"
            lines.append(f"【{info['name']}（{ticker}）】 未購入・監視中{extra}")

    # 投資信託
    for key, fund in FUNDS.items():
        lines.append(f"【{fund['name']}】 株価取得不可（欧州株・先進国株式市場の動向として確認）")

    return "\n".join(lines) if lines else "（保有銘柄なし）"


def check_hold_limits(portfolio):
    """保有期限が近い銘柄を検出"""
    alerts = []
    today = date.today()
    for ticker, info in portfolio.items():
        if info.get("max_hold_date"):
            deadline = datetime.datetime.strptime(info["max_hold_date"], "%Y-%m-%d").date()
            days_left = (deadline - today).days
            if days_left <= 14:
                alerts.append(f"{info['name']}の保有期限（{info['max_hold_date']}）まで残り{days_left}日")
    return alerts


def generate_script(news_items, market_data, portfolio_status):
    if not GEMINI_API_KEY:
        print("FAIL: GEMINI_API_KEY が設定されていません")
        return None

    news_text = "\n".join(f"- {t}" for t in news_items) if news_items else "（取得なし）"
    market_text = format_market_text(market_data)
    portfolio_text = format_portfolio_text(portfolio_status)

    # 保有期限チェック
    hold_alerts = check_hold_limits(PORTFOLIO)
    if hold_alerts:
        hold_limit_alerts_text = "【保有期限アラート】\n" + "\n".join(f"- {a}" for a in hold_alerts) + "\n※ 原稿内で保有期限が近い銘柄について注意喚起してください"
    else:
        hold_limit_alerts_text = ""

    prompt = SCRIPT_PROMPT.format(
        today=TODAY_JP,
        news_text=news_text,
        market_text=market_text,
        portfolio_text=portfolio_text,
        hold_limit_alerts=hold_limit_alerts_text,
    )

    client = genai.Client(api_key=GEMINI_API_KEY)
    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        temperature=0.7,
                        max_output_tokens=8192,
                    ),
                )

                finish_reason = None
                try:
                    finish_reason = response.candidates[0].finish_reason
                except Exception:
                    pass

                text = response.text.strip() if response.text else ""

                if finish_reason and str(finish_reason) in ("FinishReason.MAX_TOKENS", "MAX_TOKENS", "2"):
                    print(f"  NG: {model} attempt {attempt+1} → MAX_TOKENS で出力が打ち切られました（{len(text)}文字）。リトライします...")
                    time.sleep(5)
                    continue

                if len(text) < MIN_SCRIPT_LENGTH:
                    print(f"  NG: {model} attempt {attempt+1} → 原稿が短すぎます（{len(text)}文字 < {MIN_SCRIPT_LENGTH}文字）。リトライします...")
                    time.sleep(5)
                    continue

                print(f"  OK: {model} で原稿生成完了（{len(text)}文字、finish_reason={finish_reason}）")
                return text

            except Exception as e:
                err_str = str(e)
                print(f"  NG: {model} attempt {attempt+1} → {err_str[:100]}")
                if "429" in err_str or "quota" in err_str.lower() or "Resource" in err_str:
                    wait_sec = 30 * (attempt + 1)
                    print(f"  レート制限 - {wait_sec}秒待機...")
                    time.sleep(wait_sec)
                else:
                    break

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


def update_player_html(portfolio_status, today, mp3_filename):
    """ポートフォリオ付きHTMLプレーヤーを生成"""

    # カード生成
    cards_html = ""
    for s in portfolio_status:
        pnl_color = "#4CAF50" if s["pnl_pct"] >= 0 else "#FF5252"
        pnl_sign = "+" if s["pnl_pct"] >= 0 else ""
        currency_symbol = "$" if s["currency"] == "USD" else "\u00a5"

        if s.get("stop_loss"):
            sl_val = s["stop_loss"]
            sl_text = f"{currency_symbol}{sl_val:,}"
        else:
            sl_text = "\u2014"
        if s.get("take_profit"):
            tp_val = s["take_profit"]
            tp_text = f"{currency_symbol}{tp_val:,}"
        else:
            tp_text = "\u6bd4\u7387\u7ba1\u7406"

        extra = ""
        if s.get("max_hold_date"):
            extra = f'<div class="hold-limit">\u4fdd\u6709\u671f\u9650: {s["max_hold_date"]}</div>'

        cards_html += f"""
        <div class="stock-card">
            <div class="card-header">
                <div>
                    <span class="stock-name">{s['name']}</span>
                    <span class="ticker">{s['ticker']}</span>
                </div>
                <span class="category-badge">{s['category']}</span>
            </div>
            <div class="card-body">
                <div class="current-price">{currency_symbol}{s['current']:,.2f}</div>
                <div class="pnl" style="color:{pnl_color}">{pnl_sign}{s['pnl_pct']}%</div>
            </div>
            <div class="card-lines">
                <div class="line-item">
                    <span class="line-label">\u640d\u5207</span>
                    <span class="line-value sl">{sl_text}</span>
                </div>
                <div class="line-item">
                    <span class="line-label">\u5229\u78ba</span>
                    <span class="line-value tp">{tp_text}</span>
                </div>
            </div>
            {extra}
        </div>
        """

    # SBI・V・先進国ファンドのカード（株価取得対象外）
    cards_html += """
        <div class="stock-card fund-card">
            <div class="card-header">
                <div>
                    <span class="stock-name">SBI\u30fbV\u30fb\u5148\u9032\u56fd\u682a\u5f0f\uff08\u9664\u304f\u7c73\u56fd\uff09</span>
                    <span class="ticker">\u6295\u8cc7\u4fe1\u8a17</span>
                </div>
                <span class="category-badge">\u5148\u9032\u56fd\u682a\u5f0f</span>
            </div>
            <div class="card-body">
                <div class="current-price" style="font-size:14px;color:#B0B0B0">\u682a\u4fa1\u81ea\u52d5\u53d6\u5f97\u5bfe\u8c61\u5916</div>
            </div>
        </div>
    """

    now_jst = datetime.datetime.now(JST)
    last_updated = now_jst.strftime("%Y-%m-%d %H:%M JST")

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>Morning Brief - {today}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family: system-ui, -apple-system, sans-serif;
    background: linear-gradient(180deg, #1A1A2E 0%, #16213E 100%);
    color: #FFFFFF;
    min-height: 100vh;
    padding: env(safe-area-inset-top) 16px env(safe-area-inset-bottom) 16px;
  }}
  .container {{ max-width:430px; margin:0 auto; padding:24px 0; }}

  /* Header */
  .header {{ text-align:center; margin-bottom:28px; }}
  .header h1 {{ font-size:28px; font-weight:700; letter-spacing:-0.5px; }}
  .header .date {{ color:#FF6B00; font-size:14px; margin-top:4px; font-weight:500; }}

  /* Audio Player */
  .player-section {{ margin-bottom:32px; }}
  .player-section audio {{ width:100%; border-radius:12px; }}

  /* Portfolio Section */
  .section-title {{
    font-size:18px; font-weight:600; margin-bottom:16px;
    padding-left:12px; border-left:3px solid #FF6B00;
  }}
  .stock-card {{
    background: rgba(255,255,255,0.06);
    border-radius:12px; padding:16px;
    margin-bottom:12px;
    border: 1px solid rgba(255,255,255,0.08);
  }}
  .card-header {{
    display:flex; justify-content:space-between; align-items:flex-start;
    margin-bottom:12px;
  }}
  .stock-name {{ font-size:15px; font-weight:600; display:block; }}
  .ticker {{ font-size:12px; color:#B0B0B0; }}
  .category-badge {{
    font-size:10px; background:#FF6B00; color:#fff;
    padding:2px 8px; border-radius:10px; white-space:nowrap;
    font-weight:500;
  }}
  .card-body {{
    display:flex; justify-content:space-between; align-items:baseline;
    margin-bottom:12px;
  }}
  .current-price {{ font-size:22px; font-weight:700; }}
  .pnl {{ font-size:16px; font-weight:600; }}
  .card-lines {{
    display:flex; gap:12px;
  }}
  .line-item {{
    flex:1; background:rgba(255,255,255,0.04);
    border-radius:8px; padding:8px 10px; text-align:center;
  }}
  .line-label {{ display:block; font-size:10px; color:#B0B0B0; margin-bottom:2px; }}
  .line-value {{ display:block; font-size:14px; font-weight:600; }}
  .line-value.sl {{ color:#FF5252; }}
  .line-value.tp {{ color:#4CAF50; }}
  .hold-limit {{
    margin-top:8px; font-size:11px; color:#FF6B00;
    background:rgba(255,107,0,0.1); border-radius:6px;
    padding:4px 8px; text-align:center;
  }}
  .fund-card {{ opacity:0.7; }}

  /* Footer */
  .footer {{
    text-align:center; margin-top:32px; padding-top:16px;
    border-top:1px solid rgba(255,255,255,0.08);
  }}
  .footer .updated {{ font-size:12px; color:#B0B0B0; }}
  .footer .disclaimer {{ font-size:10px; color:#666; margin-top:8px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Morning Brief</h1>
    <div class="date">{today}</div>
  </div>

  <div class="player-section">
    <audio controls preload="metadata">
      <source src="{mp3_filename}" type="audio/mpeg">
    </audio>
  </div>

  <div class="section-title">Portfolio</div>
  {cards_html}

  <div class="footer">
    <div class="updated">Last updated: {last_updated}</div>
    <div class="disclaimer">\u203b\u6295\u8cc7\u5224\u65ad\u306f\u3054\u81ea\u8eab\u306e\u8cac\u4efb\u3067\u884c\u3063\u3066\u304f\u3060\u3055\u3044</div>
  </div>
</div>
</body>
</html>"""

    index_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  OK: index.html \u66f4\u65b0 \u2192 {index_path}")


def main():
    print("=" * 50)
    print("Morning News Generator - " + TODAY_JP)
    print("=" * 50)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("\n[1] ニュース取得中...")
    news_items = fetch_rss(RSS_FEEDS)
    print(f"  取得: {len(news_items)}件")

    if not news_items:
        print("FAIL: ニュースが取得できませんでした")
        exit(1)

    print("\n[2] 株価取得中（ポートフォリオ銘柄）...")
    stock_data = fetch_stock_prices()
    print(f"  取得: {stock_data}")

    print("\n[3] マーケット指標取得中...")
    market_data = fetch_market_indices()
    print(f"  取得: {market_data}")

    print("\n[4] ポートフォリオ損益計算中...")
    portfolio_status = calculate_portfolio_status(PORTFOLIO, stock_data)
    for s in portfolio_status:
        alert_str = f" ★{s['alert']}★" if s["alert"] else ""
        print(f"  {s['name']}: {s['pnl_pct']:+.1f}%{alert_str}")

    print("\n[5] 原稿生成中...")
    script = generate_script(news_items, market_data, portfolio_status)
    if not script:
        print("\nFAIL: 原稿生成に失敗しました")
        exit(1)

    script_path = os.path.join(OUTPUT_DIR, "script_" + TODAY + ".txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    print(f"  OK: 原稿保存完了（{len(script)}文字）")

    print("\n[6] 音声生成中...")
    success = asyncio.run(generate_audio(script, OUTPUT_MP3))
    if success:
        # HTML用にstop_loss/take_profit/max_hold_date情報を付加
        portfolio_status_for_html = []
        for s in portfolio_status:
            ticker = s["ticker"]
            info = PORTFOLIO.get(ticker, {})
            s["stop_loss"] = info.get("stop_loss")
            s["take_profit"] = info.get("take_profit")
            s["max_hold_date"] = info.get("max_hold_date")
            portfolio_status_for_html.append(s)

        update_player_html(portfolio_status_for_html, TODAY, "podcast.mp3")
        print("\nDONE!")
    else:
        exit(1)


if __name__ == "__main__":
    main()
