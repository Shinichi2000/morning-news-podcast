import os
import re
import json
import glob
import datetime
import asyncio
import time
import random
from collections import Counter

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

PORTFOLIO_PATH = "portfolio.json"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# クォータに優しい軽量モデルを優先し、徐々に上位モデルへフォールバックする。
# 無料枠では flash-lite 系のレート上限が比較的緩いため最優先に置く。
GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.5-pro",
]

# 1モデルあたりの最大リトライ回数
MAX_RETRIES_PER_MODEL = 4

# 原稿の最低文字数（これを下回る場合は生成失敗と見なす）
MIN_SCRIPT_LENGTH = 1500

# ===== ポートフォリオ（portfolio.json が唯一の正）=====

def load_portfolio(path=PORTFOLIO_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def active_holdings(portfolio):
    """株価取得・原稿・サイト表示の対象銘柄（未購入 shares==0 と投資信託を除外）"""
    return [
        h for h in portfolio["holdings"]
        if h.get("shares", 0) > 0 and not h.get("is_fund") and h.get("ticker")
    ]

def fund_holdings(portfolio):
    return [h for h in portfolio["holdings"] if h.get("is_fund")]

# ===== 日替わり構成エンジン =====

WEEKDAY_THEMES = {
    0: {  # 月曜
        "name": "ウィークリー・プレビュー",
        "focus": "今週の経済イベント・決算予定・注目テーマを展望する。先週の振り返りは短く、今週何が動くかに重点を置く。",
    },
    1: {  # 火曜
        "name": "グローバル・フォーカス",
        "focus": "国際情勢・地政学を深掘りする日。中東情勢、米中、欧州を中心に、保有銘柄への波及を解説。",
    },
    2: {  # 水曜
        "name": "マーケット・メカニクス",
        "focus": "相場の仕組みや投資テーマを1つ取り上げて解説する教養回。金利と株価、為替メカニズムなど。",
    },
    3: {  # 木曜
        "name": "セクター・ディープダイブ",
        "focus": "保有銘柄が属するセクター（防衛、半導体、金融、コモディティ）のいずれかを日替わりで深掘り。",
    },
    4: {  # 金曜
        "name": "ウィークリー・ラップアップ",
        "focus": "今週の値動きを総括し、保有ポートフォリオの週間パフォーマンスをレビュー。来週への申し送り。",
    },
    5: {  # 土曜
        "name": "リバランス・レビュー",
        "focus": "月初第1土曜はリバランス日。保有比率・ライン更新の確認を促す。それ以外の土曜は週末の落ち着いた長尺解説。",
    },
    6: {  # 日曜
        "name": "ロング・ビュー",
        "focus": "短期ニュースから離れ、長期的な投資テーマや1週間の総括を落ち着いたトーンで。",
    },
}

ROTATING_SEGMENTS = [
    "今日の数字：その日のニュースから象徴的な数字を1つ取り上げ、背景を解説",
    "保有銘柄スポットライト：保有銘柄から1つを日替わりで取り上げ、最新動向と論点を深掘り",
    "投資用語の基礎：1つの専門用語をやさしく解説（コンタンゴ、イールドカーブ等）",
    "ヒストリー：過去の同じ日に市場で起きた出来事と、そこから得られる教訓",
    "ウォッチリスト：保有していないが注目すべき銘柄・指標を1つ紹介",
    "クオート：著名投資家の言葉を1つ引用し、現在の相場に当てはめて考える",
    "コントラリアン：その日の市場コンセンサスに対し、あえて反対側の視点を提示する",
]

OPENING_STYLES = [
    "その日の最大の論点を問いかけから始める（例：『今朝、市場が最も気にしているのは何か』）",
    "前日の最も印象的な値動きの数字から入る",
    "天候や季節の比喩で相場の雰囲気を一言で表現してから本題へ（※気象情報そのものは入れない）",
    "今日が何の日か（経済史上の出来事）から入る",
    "端的に『今日の3つのポイント』を予告してから始める",
]

def is_first_saturday(d):
    return d.weekday() == 5 and d.day <= 7

def get_weekday_theme(d):
    theme = dict(WEEKDAY_THEMES[d.weekday()])
    if is_first_saturday(d):
        theme["focus"] += "【本日は月初第1土曜＝リバランス日です。クロージングで「本日はリバランス日です。逆指値の更新を忘れずに」と必ず促してください】"
    return theme

def pick_segment(d, holdings):
    """日付から決定的に特集を選ぶ（7日周期で循環し重複しない）"""
    day_index = d.toordinal()
    segment = ROTATING_SEGMENTS[day_index % len(ROTATING_SEGMENTS)]
    # 「保有銘柄スポットライト」が選ばれた日は、取り上げる銘柄も日替わりで決定
    if "スポットライト" in segment and holdings:
        spotlight = holdings[day_index % len(holdings)]
        segment += f"（本日の対象：{spotlight['name']}）"
    return segment

def pick_opening_style(d):
    return OPENING_STYLES[d.toordinal() % len(OPENING_STYLES)]

def extract_recent_phrases(output_dir=OUTPUT_DIR, days=7, max_phrases=15):
    """直近の生成原稿から頻出フレーズ（特にオープニング・クロージング・定型文）を抽出"""
    files = sorted(glob.glob(os.path.join(output_dir, "script_*.txt")))[-days:]
    phrases = []
    counter = Counter()
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
        except Exception:
            continue
        if not text:
            continue
        sentences = [s.strip() for s in re.split(r"[。\n]", text) if s.strip()]
        if sentences:
            # 冒頭と末尾の文（オープニング・クロージング）はそのまま回避対象に
            phrases.append(sentences[0][:40])
            phrases.append(sentences[-1][:40])
        # 文頭の言い回し（先頭14文字）を集計し、複数日で繰り返されたものを抽出
        for s in sentences:
            if len(s) >= 10:
                counter[s[:14]] += 1
    for prefix, count in counter.most_common(20):
        if count >= 2:
            phrases.append(prefix)
    # 重複除去・上限
    seen = []
    for p in phrases:
        if p not in seen:
            seen.append(p)
    return seen[:max_phrases]

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

SCRIPT_PROMPT = """あなたは経済情報番組の構成作家兼ナレーターです。
毎朝配信する、リスナーを飽きさせない6〜10分のラジオ番組の原稿を日本語で作成してください。
このリスナーは25歳の建築設計者で、投資をアクティブに運用しており、知的な深掘りを好みます。

【本日の番組テーマ】（曜日による）
{weekday_theme_name}：{weekday_theme_focus}

【本日のオープニングの入り方】
{opening_style}

【本日のローテーション特集コーナー】
{rotating_segment}

【番組構成 — 以下を基本としつつ、曜日テーマに応じて各パートの長さを変えてよい】

1. オープニング（指定された入り方で。キャスター名の自己紹介はしない。「皆さん」等の複数呼びかけもしない。リスナーは一人）

2. 国際ニュース（火曜は厚め、それ以外は標準）
- 各ニュースは「何が起きたか」+「なぜ重要か」+「市場への含意」をセットで
- 中東情勢は保有銘柄（金、LMT）への波及とセットで語る

3. 国内ニュース
- 日銀・為替・日経平均は必ず触れる。金融政策は三菱UFJへの含意とセットで

4. マーケット概況（数値には必ず「いつ時点」かを付す）

5. 本日のローテーション特集コーナー（上記指定のコーナーをここで展開。2〜3分）

6. 保有ポートフォリオ報告
- 全銘柄を機械的に読まない。以下の優先順位で2〜4銘柄を選ぶ：
  a. アラート銘柄（損切り/利確ラインまで5%以内）は必ず
  b. 前日比±2%以上の銘柄
  c. 本日のニュースに関連する銘柄
- 各銘柄は「取得日（as_of）時点の終値」「損益率」「損切り/利確ラインの価格とそこまでの距離（円/ドルと％）」を読む
- ライン距離は「損切り480ドルまであと約50ドル、現在値から9パーセント下」のように価格と％の両方を読み上げ、片方だけに依存しない
- staleフラグのある銘柄は「最新データが取得できず○月○日時点」と明示
- 金曜は全銘柄の週間サマリー

7. クロージング
- 今週/翌営業日の重要経済指標・イベントを日付とともに予告
- 第1土曜の回は「本日はリバランス日です。逆指値の更新を忘れずに」と促す

【禁止事項】
- 天気・気温・花粉・服装・芸能・スポーツは入れない
- 「買うべき」「売るべき」等の投資助言表現は使わない
- 毎日同じ言い回しのオープニング・クロージングを避ける
- メタ情報（AI生成である旨など）は入れない

【直近の放送で使った表現リスト — マンネリ化を避けるため、これらの言い回しは避けること】
{avoid_phrases}

【話し方】
- 自然な話し言葉。番組テーマに応じてトーンを変える（火曜は緊張感、日曜は落ち着いた語り）
- 数値は正負を明示。銘柄は日本語名
- 音声合成で読み上げるため、読み間違いされにくい表記にする：
  - アルファベット略語はカタカナで書く（例：「FOMC」→「エフオーエムシー」、「CPI」→「シーピーアイ」、「NASDAQ」→「ナスダック」）
  - 記号は使わず言葉で書く（「%」→「パーセント」、「+5%」→「プラス5パーセント」、「1ドル=160円」→「1ドル160円」）
  - 誤読されやすい漢字はひらがなで書く（「逆指値」→「ぎゃくさしね」、「金先物」→「きん先物」）
- パート間の切り替えは自然なつなぎ言葉を使い、見出しや番号は読まない
- 原稿のみ出力する（メタ情報・注釈・マークダウン記法は不要）

【ニュース素材】
{news_text}

【マーケットデータ】
{market_text}

【保有ポートフォリオ状況（検証済みデータ）】
{portfolio_text}

【本日の日付・曜日】
{today}（{weekday_jp}）
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

# ===== 株価取得の堅牢化（課題1）=====

def fetch_price_robust(ticker):
    """
    終値を堅牢に取得する。
    - 直近5営業日を取得し、最新の有効な終値を採用
    - データの鮮度（最終取引日）も返す
    - 異常値（None/0/負値）は無効として扱う
    """
    try:
        data = yf.Ticker(ticker).history(period="5d", interval="1d")
        if data.empty:
            return {"price": None, "as_of": None, "stale": True, "error": "データ取得不可"}

        valid = data["Close"].dropna()
        valid = valid[valid > 0]
        if valid.empty:
            return {"price": None, "as_of": None, "stale": True, "error": "有効な終値なし"}

        last_price = float(valid.iloc[-1])
        last_date = valid.index[-1].to_pydatetime()

        days_old = (datetime.datetime.now() - last_date.replace(tzinfo=None)).days
        stale = days_old > 3

        return {
            "price": round(last_price, 2),
            "as_of": last_date.strftime("%Y-%m-%d"),
            "stale": stale,
            "error": None,
        }
    except Exception as e:
        return {"price": None, "as_of": None, "stale": True, "error": str(e)}

def fetch_market_indices():
    """マーケット指標を取得（as_of・stale付き）"""
    result = {}
    for ticker, label in MARKET_INDICES.items():
        info = fetch_price_robust(ticker)
        if info["price"] is not None:
            result[label] = info
        else:
            print(f"  指標取得エラー ({label}): {info['error']}")
    return result

# ===== 損益・ライン距離計算の明示化と検証（課題1）=====

def calc_position(holding, price_info):
    """1銘柄の損益とライン距離を計算。検証可能な形で全項目を返す"""
    if holding["shares"] == 0 or price_info["price"] is None:
        return None

    price = price_info["price"]
    cost = holding["cost"]
    cur = holding["currency"]
    sym = "$" if cur == "USD" else "¥"

    pnl_pct = round((price - cost) / cost * 100, 1)
    market_value = round(price * holding["shares"], 2)
    pnl_amount = round((price - cost) * holding["shares"], 2)

    result = {
        "name": holding["name"],
        "ticker": holding["ticker"],
        "thesis": holding["thesis"],
        "currency": cur, "symbol": sym,
        "price": price, "cost": cost,
        "shares": holding["shares"],
        "as_of": price_info["as_of"],
        "stale": price_info["stale"],
        "pnl_pct": pnl_pct,
        "market_value": market_value,
        "pnl_amount": pnl_amount,
        "stop_loss": holding.get("stop_loss"),
        "take_profit": holding.get("take_profit"),
        "note": holding.get("note", ""),
    }

    if holding.get("stop_loss"):
        sl = holding["stop_loss"]
        result["sl_gap_price"] = round(price - sl, 2)
        result["sl_gap_pct"] = round((price - sl) / price * 100, 1)
        result["sl_alert"] = result["sl_gap_pct"] < 5
    if holding.get("take_profit"):
        tp = holding["take_profit"]
        result["tp_gap_price"] = round(tp - price, 2)
        result["tp_gap_pct"] = round((tp - price) / price * 100, 1)
        result["tp_alert"] = result["tp_gap_pct"] < 5

    return result

def build_positions(portfolio):
    """全アクティブ銘柄の価格取得＋損益計算"""
    positions = []
    for h in active_holdings(portfolio):
        info = fetch_price_robust(h["ticker"])
        if info["price"] is None:
            print(f"  株価取得エラー ({h['ticker']}): {info['error']}")
            positions.append({
                "name": h["name"], "ticker": h["ticker"], "thesis": h["thesis"],
                "currency": h["currency"], "symbol": "$" if h["currency"] == "USD" else "¥",
                "price": None, "as_of": None, "stale": True,
                "error": info["error"],
                "stop_loss": h.get("stop_loss"), "take_profit": h.get("take_profit"),
            })
            continue
        pos = calc_position(h, info)
        if pos:
            positions.append(pos)
    return positions

def fmt_num(value, currency):
    if value is None:
        return "—"
    if currency == "JPY":
        return f"{value:,.0f}" if float(value) == int(value) else f"{value:,.2f}"
    return f"{value:,.2f}"

def format_market_text(market_data):
    lines = []
    for label, info in market_data.items():
        price = info["price"]
        stale_note = "・最新データ取得できず" if info["stale"] else ""
        as_of = f"（{info['as_of']}時点{stale_note}）"
        if label == "ドル円":
            lines.append(f"{label}: {price:.2f}円 {as_of}")
        elif label in ("WTI原油先物", "金先物"):
            lines.append(f"{label}: ${price:,.2f} {as_of}")
        elif label in ("S&P 500", "NASDAQ"):
            lines.append(f"{label}: {price:,.2f} {as_of}")
        else:
            lines.append(f"{label}: {price:,.2f}円 {as_of}")
    return "\n".join(lines) if lines else "（取得なし）"

def format_portfolio_text(positions, portfolio):
    lines = []
    for p in positions:
        sym = p["symbol"]
        cur = p["currency"]
        unit = "円" if cur == "JPY" else "ドル"
        if p.get("price") is None:
            lines.append(f"【{p['name']}（{p['ticker']}）】 株価取得失敗（{p.get('error', '不明')}）。本日は数値に言及しないこと")
            continue
        stale_note = "・最新データ取得できず" if p["stale"] else ""
        line = (
            f"【{p['name']}（{p['ticker']}）/ テーゼ: {p['thesis']}】"
            f" 終値: {sym}{fmt_num(p['price'], cur)}（as_of: {p['as_of']}{stale_note}）"
            f" / 取得単価: {sym}{fmt_num(p['cost'], cur)}"
            f" / 損益率: {p['pnl_pct']:+.1f}%"
            f" / 評価額: {sym}{fmt_num(p['market_value'], cur)}"
            f" / 損益額: {'+' if p['pnl_amount'] >= 0 else ''}{fmt_num(p['pnl_amount'], cur)}{unit}"
        )
        if p.get("stop_loss"):
            line += (
                f" / 損切りライン: {sym}{fmt_num(p['stop_loss'], cur)}"
                f"（あと{fmt_num(p['sl_gap_price'], cur)}{unit}・現在値から{p['sl_gap_pct']}%下）"
            )
            if p.get("sl_alert"):
                line += " ★損切りライン接近（5%以内）★"
        if p.get("take_profit"):
            line += (
                f" / 利確ライン: {sym}{fmt_num(p['take_profit'], cur)}"
                f"（あと{fmt_num(p['tp_gap_price'], cur)}{unit}・現在値から{p['tp_gap_pct']}%上）"
            )
            if p.get("tp_alert"):
                line += " ★利確ライン接近（5%以内）★"
        if p.get("note"):
            line += f" / メモ: {p['note']}"
        lines.append(line)

    for fund in fund_holdings(portfolio):
        lines.append(
            f"【{fund['name']}】 投資信託のため株価取得不可。"
            f"欧州株・先進国株式（除く米国）市場の動向として伝える。メモ: {fund.get('note', '')}"
        )

    return "\n".join(lines) if lines else "（保有銘柄なし）"

def _classify_error(err_str):
    """例外メッセージから、リトライ可能な一時エラーかを判定する"""
    s = err_str.lower()
    # クォータ/レート制限（429）
    if "429" in err_str or "quota" in s or "resource_exhausted" in s or "rate limit" in s or "ratelimit" in s:
        return "rate_limit"
    # サーバ側の一時的過負荷・障害（500/502/503/504）
    if ("503" in err_str or "unavailable" in s or "overloaded" in s
            or "500" in err_str or "internal" in s
            or "502" in err_str or "504" in err_str
            or "deadline" in s or "timeout" in s):
        return "server"
    return "fatal"

def generate_script(news_items, market_data, positions, portfolio):
    if not GEMINI_API_KEY:
        print("FAIL: GEMINI_API_KEY が設定されていません")
        return None

    news_text = "\n".join(f"- {t}" for t in news_items) if news_items else "（取得なし）"
    market_text = format_market_text(market_data)
    portfolio_text = format_portfolio_text(positions, portfolio)

    today_date = NOW_JST.date()
    theme = get_weekday_theme(today_date)
    segment = pick_segment(today_date, active_holdings(portfolio))
    opening_style = pick_opening_style(today_date)
    recent_phrases = extract_recent_phrases()
    avoid_text = "\n".join(f"- {p}" for p in recent_phrases) if recent_phrases else "（なし）"

    print(f"  曜日テーマ: {theme['name']}")
    print(f"  特集コーナー: {segment[:30]}...")
    print(f"  オープニング: {opening_style[:30]}...")
    print(f"  回避フレーズ: {len(recent_phrases)}件")

    prompt = SCRIPT_PROMPT.format(
        weekday_theme_name=theme["name"],
        weekday_theme_focus=theme["focus"],
        opening_style=opening_style,
        rotating_segment=segment,
        avoid_phrases=avoid_text,
        news_text=news_text,
        market_text=market_text,
        portfolio_text=portfolio_text,
        today=NOW_JST.strftime("%Y年%m月%d日"),
        weekday_jp=WEEKDAYS_JP[NOW_JST.weekday()],
    )

    client = genai.Client(api_key=GEMINI_API_KEY)

    # モデルを順に試し、各モデルで一時エラー時は指数バックオフ＋ジッターでリトライ。
    # 429/503/500 系はすべてリトライ対象とし、全モデルを2巡する。
    for sweep in range(2):
        for model in GEMINI_MODELS:
            for attempt in range(MAX_RETRIES_PER_MODEL):
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
                        print(f"  NG: {model} sweep {sweep+1} attempt {attempt+1} → MAX_TOKENS で出力が打ち切られました（{len(text)}文字）。リトライします...")
                        time.sleep(5)
                        continue

                    if len(text) < MIN_SCRIPT_LENGTH:
                        print(f"  NG: {model} sweep {sweep+1} attempt {attempt+1} → 原稿が短すぎます（{len(text)}文字 < {MIN_SCRIPT_LENGTH}文字）。リトライします...")
                        time.sleep(5)
                        continue

                    print(f"  OK: {model} で原稿生成完了（{len(text)}文字、finish_reason={finish_reason}）")
                    return text

                except Exception as e:
                    err_str = str(e)
                    kind = _classify_error(err_str)
                    print(f"  NG: {model} sweep {sweep+1} attempt {attempt+1} → [{kind}] {err_str[:120]}")

                    if kind == "fatal":
                        # 認証エラー・モデル名不正など。このモデルはこれ以上試さず次モデルへ
                        break

                    if attempt == MAX_RETRIES_PER_MODEL - 1:
                        # このモデルのリトライ上限。次モデルへフォールバック
                        break

                    # 指数バックオフ＋ジッター。rate_limit はやや長めに待つ
                    base = 20 if kind == "rate_limit" else 8
                    wait_sec = min(base * (2 ** attempt), 120) + random.uniform(0, 5)
                    print(f"  {kind} - {wait_sec:.0f}秒待機して再試行...")
                    time.sleep(wait_sec)

        # 1巡目で全滅した場合、少し長く待ってから2巡目（クォータ回復・過負荷解消を期待）
        if sweep == 0:
            print("  全モデルで失敗。90秒待機して全モデルを再試行します...")
            time.sleep(90)

    return None

# ===== TTS読み上げ正規化（読み間違い対策）=====
# 読み間違いに気づいたら、このリストに（誤読される表記, 正しい読み）を追記するだけで直せる。
# 上から順に置換されるため、長い表記を先に書くこと。
TTS_READING_FIXES = [
    ("S&P500", "エスアンドピー500"),
    ("S&P 500", "エスアンドピー500"),
    ("S&P", "エスアンドピー"),
    ("NASDAQ", "ナスダック"),
    ("Nasdaq", "ナスダック"),
    ("NYSE", "ニューヨーク証券取引所"),
    ("NYダウ", "ニューヨークダウ"),
    ("OPEC", "オペック"),
    ("NATO", "ナトー"),
    ("NVDA", "エヌビディア"),
    ("LMT", "ロッキード・マーチン"),
    ("REIT", "リート"),
    ("FRB", "エフアールビー"),
    # 金融用語の誤読対策（漢字をかなに開く）
    ("金先物", "きん先物"),
    ("金価格", "きん価格"),
    ("金相場", "きん相場"),
    ("逆指値", "ぎゃくさしね"),
    ("指値", "さしね"),
    ("約定", "やくじょう"),
    ("寄り付き", "よりつき"),
    ("大引け", "おおびけ"),
    ("前引け", "ぜんびけ"),
    ("値幅", "ねはば"),
    ("上値", "うわね"),
    ("下値", "したね"),
]

# 英大文字略語を一文字ずつカタカナに読み下すための表
ALPHA_KATAKANA = {
    "A": "エー", "B": "ビー", "C": "シー", "D": "ディー", "E": "イー",
    "F": "エフ", "G": "ジー", "H": "エイチ", "I": "アイ", "J": "ジェー",
    "K": "ケー", "L": "エル", "M": "エム", "N": "エヌ", "O": "オー",
    "P": "ピー", "Q": "キュー", "R": "アール", "S": "エス", "T": "ティー",
    "U": "ユー", "V": "ブイ", "W": "ダブリュー", "X": "エックス",
    "Y": "ワイ", "Z": "ゼット",
}


def normalize_for_tts(text):
    """音声合成前に、誤読されやすい表記を読み上げ用に正規化する。
    保存される原稿（script_*.txt）は元の表記のまま、音声だけに適用する。"""
    t = text
    # 装飾記号・マークダウン残骸の除去
    t = re.sub(r"[■◆●▼★☆*#`_]+", "", t)
    # 個別の読み修正
    for wrong, right in TTS_READING_FIXES:
        t = t.replace(wrong, right)
    # 記号の読み下し
    t = t.replace("％", "パーセント").replace("%", "パーセント")
    t = t.replace("±", "プラスマイナス")
    t = t.replace("&", "アンド")
    t = t.replace("＝", "、").replace("=", "、")
    t = re.sub(r"[〜~](?=[0-9])", "から", t)
    # 通貨記号は数値の後ろに読み替える（¥19,400 → 19,400円 / $68.39 → 68.39ドル）
    t = re.sub(r"[¥￥]([0-9][0-9,\.]*)", r"\1円", t)
    t = re.sub(r"\$([0-9][0-9,\.]*)", r"\1ドル", t)
    # 数値の正負記号（日付の「2026-06-10」等は前が数字なので対象外）
    t = re.sub(r"(?<![0-9])[+＋](?=[0-9])", "プラス", t)
    t = re.sub(r"(?<![0-9\-])[\-−▲](?=[0-9])", "マイナス", t)
    # Q1〜Q4 → 第N四半期
    t = re.sub(r"(?<![A-Za-z0-9])Q([1-4])(?![0-9])", r"第\1四半期", t)
    # 残った英大文字略語（2〜5文字）は一文字ずつカタカナで読む（例: FOMC→エフオーエムシー）
    def _acro(m):
        return "".join(ALPHA_KATAKANA[c] for c in m.group(0))
    t = re.sub(r"(?<![A-Za-z0-9])[A-Z]{2,5}(?![A-Za-z0-9])", _acro, t)
    return t


async def generate_audio(script, output_path):
    try:
        tts_text = normalize_for_tts(script)
        communicate = edge_tts.Communicate(tts_text, VOICE)
        await communicate.save(output_path)
        print(f"  OK: 音声生成完了 → {output_path}")
        return True
    except Exception as e:
        print(f"  FAIL: 音声生成エラー → {e}")
        return False

# ===== サイト用データ・HTML生成（課題3）=====

def list_recent_scripts(limit=7):
    """サイトのアーカイブ表示用に、残っている原稿ファイルを新しい順で返す"""
    files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "script_*.txt")), reverse=True)[:limit]
    out = []
    for f in files:
        m = re.search(r"script_(\d{4}-\d{2}-\d{2})\.txt$", f)
        if m:
            out.append({"date": m.group(1), "file": os.path.basename(f)})
    return out


def build_dashboard_data(positions, market_data, portfolio, mp3_filename, audio_ok):
    """app.js が fetch して描画する dashboard.json を構築"""
    today_date = NOW_JST.date()
    theme = get_weekday_theme(today_date)
    segment = pick_segment(today_date, active_holdings(portfolio))
    segment_title = segment.split("：")[0]
    segment_detail = segment.split("（本日の対象：")
    spotlight = segment_detail[1].rstrip("）") if len(segment_detail) > 1 else None

    totals = {}
    for p in positions:
        if p.get("price") is None:
            continue
        cur = p["currency"]
        t = totals.setdefault(cur, {"market_value": 0.0, "pnl_amount": 0.0, "count": 0})
        t["market_value"] = round(t["market_value"] + p["market_value"], 2)
        t["pnl_amount"] = round(t["pnl_amount"] + p["pnl_amount"], 2)
        t["count"] += 1

    # ドル円レートがあれば円換算の総資産も算出（検証可能なようにレートも出力）
    usdjpy = None
    if "ドル円" in market_data and market_data["ドル円"]["price"]:
        usdjpy = market_data["ドル円"]["price"]
    total_jpy = None
    if usdjpy is not None:
        total_jpy = round(
            totals.get("JPY", {}).get("market_value", 0)
            + totals.get("USD", {}).get("market_value", 0) * usdjpy
        )

    market_list = [
        {"label": label, "price": info["price"], "as_of": info["as_of"], "stale": info["stale"]}
        for label, info in market_data.items()
    ]

    excluded = [
        {"name": h["name"], "ticker": h.get("ticker"), "note": h.get("note", "")}
        for h in portfolio["holdings"]
        if h.get("shares", 0) == 0 and not h.get("is_fund")
    ]
    funds = [
        {"name": h["name"], "thesis": h.get("thesis", ""), "note": h.get("note", "")}
        for h in fund_holdings(portfolio)
    ]

    scripts = list_recent_scripts()
    script_file = next((s["file"] for s in scripts if s["date"] == TODAY), None)

    return {
        "date": TODAY,
        "date_jp": TODAY_JP,
        "weekday_theme": theme["name"],
        "rotating_segment": segment_title,
        "spotlight": spotlight,
        "is_rebalance_day": is_first_saturday(today_date),
        "audio": mp3_filename,
        "audio_generated": audio_ok,
        "script_file": script_file,
        "recent_scripts": scripts,
        "generated_at": NOW_JST.strftime("%Y-%m-%d %H:%M JST"),
        "market": market_list,
        "usdjpy": usdjpy,
        "totals": totals,
        "total_jpy": total_jpy,
        "positions": positions,
        "funds": funds,
        "excluded": excluded,
    }


def update_site_data(positions, market_data, portfolio, mp3_filename, audio_ok=True):
    """dashboard.json の生成と portfolio.json の docs/ への同期"""
    dashboard = build_dashboard_data(positions, market_data, portfolio, mp3_filename, audio_ok)
    dash_path = os.path.join(OUTPUT_DIR, "dashboard.json")
    with open(dash_path, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)
    print(f"  OK: dashboard.json 更新 → {dash_path}")

    sync_path = os.path.join(OUTPUT_DIR, "portfolio.json")
    with open(PORTFOLIO_PATH, "r", encoding="utf-8") as src:
        content = src.read()
    with open(sync_path, "w", encoding="utf-8") as dst:
        dst.write(content)
    print(f"  OK: portfolio.json 同期 → {sync_path}")

def main():
    print("=" * 50)
    print("Morning News Generator v4 - " + TODAY_JP)
    print("=" * 50)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("\n[0] ポートフォリオ読み込み中...")
    portfolio = load_portfolio()
    actives = active_holdings(portfolio)
    print(f"  保有銘柄: {len(actives)}件（全{len(portfolio['holdings'])}件中、未購入・投信を除く）")

    print("\n[1] ニュース取得中...")
    news_items = fetch_rss(RSS_FEEDS)
    print(f"  取得: {len(news_items)}件")

    if not news_items:
        print("FAIL: ニュースが取得できませんでした")
        exit(1)

    print("\n[2] 株価取得・損益計算中（堅牢化版）...")
    positions = build_positions(portfolio)
    for p in positions:
        if p.get("price") is None:
            print(f"  {p['name']}: 取得失敗")
            continue
        alerts = []
        if p.get("sl_alert"):
            alerts.append("損切り接近")
        if p.get("tp_alert"):
            alerts.append("利確接近")
        stale_str = " [stale]" if p["stale"] else ""
        alert_str = f" ★{'/'.join(alerts)}★" if alerts else ""
        print(f"  {p['name']}: {p['symbol']}{p['price']:,} ({p['as_of']}){stale_str} {p['pnl_pct']:+.1f}%{alert_str}")

    print("\n[3] マーケット指標取得中...")
    market_data = fetch_market_indices()
    print(f"  取得: {len(market_data)}指標")

    print("\n[4] 原稿生成中...")
    script = generate_script(news_items, market_data, positions, portfolio)
    if not script:
        print("\nFAIL: 原稿生成に失敗しました")
        # 音声は作れなくても、価格ダッシュボードだけは最新化してサイトに反映する
        update_site_data(positions, market_data, portfolio, "podcast.mp3", audio_ok=False)
        exit(1)

    script_path = os.path.join(OUTPUT_DIR, "script_" + TODAY + ".txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    print(f"  OK: 原稿保存完了（{len(script)}文字）")

    print("\n[5] 音声生成中...")
    success = asyncio.run(generate_audio(script, OUTPUT_MP3))
    if not success:
        update_site_data(positions, market_data, portfolio, "podcast.mp3", audio_ok=False)
        exit(1)

    print("\n[6] サイトデータ更新中...")
    update_site_data(positions, market_data, portfolio, "podcast.mp3", audio_ok=True)
    print("\nDONE!")

if __name__ == "__main__":
    main()
