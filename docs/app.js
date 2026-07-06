/* Morning Brief v4 — ダッシュボード描画 + ポートフォリオ編集 */
(function () {
  "use strict";

  var REPO_DEFAULT = "shinichi2000/morning-news-podcast";
  var LS_PAT = "mb_github_pat";
  var LS_REPO = "mb_github_repo";
  var THESES = [
    "地政学ヘッジ",
    "防衛・再エスカレーション",
    "AI・半導体成長",
    "日銀利上げ受益",
    "米国外分散",
    "その他",
  ];

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtNum(v, cur) {
    if (v == null) return "—";
    var digits = (cur === "JPY" && Number.isInteger(v)) ? 0 : 2;
    return v.toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function sym(cur) { return cur === "USD" ? "$" : "¥"; }
  function pnlClass(v) { return v >= 0 ? "pos" : "neg"; }
  function signed(v) { return (v >= 0 ? "+" : "") + v; }

  /* ===================== ダッシュボード（index.html） ===================== */

  function renderDashboard() {
    fetch("dashboard.json?t=" + Date.now())
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) {
        $("date").textContent = d.date_jp || d.date;
        $("themeBadge").textContent = d.weekday_theme || "";
        var seg = d.rotating_segment || "";
        if (d.spotlight) seg += "（" + d.spotlight + "）";
        if (d.is_rebalance_day) seg += "／本日はリバランス日";
        $("segment").textContent = seg;
        if (d.audio) $("player").src = d.audio + "?t=" + encodeURIComponent(d.date || "");
        $("updated").textContent = "Last updated: " + (d.generated_at || "");
        renderNotice(d);
        renderMarket(d);
        renderSummary(d);
        renderHoldings(d);
        renderScriptBox(d);
        renderArchive(d);
      })
      .catch(function (e) {
        $("holdings").innerHTML =
          '<div class="stock-card"><div class="fund-note">ダッシュボードデータがまだ生成されていません。次回の自動実行後に表示されます。（' +
          esc(e.message) + "）</div></div>";
      });
  }

  function jstToday() {
    var jst = new Date(Date.now() + 9 * 3600 * 1000);
    return jst.toISOString().slice(0, 10);
  }

  function renderNotice(d) {
    var msgs = [];
    if (d.date && d.date !== jstToday()) {
      msgs.push("表示中のデータは " + d.date + " 生成分です（本日分はまだ生成されていません）");
    }
    if (d.audio_generated === false) {
      msgs.push("この日の音声生成は失敗しました。価格データは最新ですが、プレーヤーは前回配信分です。設定画面の「配信を再生成」で再実行できます");
    }
    $("notice").innerHTML = msgs.map(function (m) {
      return '<div class="notice warn">⚠ ' + esc(m) + "</div>";
    }).join("");
  }

  function renderMarket(d) {
    var m = d.market || [];
    if (!m.length) { $("market").innerHTML = ""; return; }
    var html = m.map(function (x) {
      var val;
      if (x.label === "ドル円") val = x.price.toFixed(2) + "円";
      else if (x.label === "WTI原油先物" || x.label === "金先物") val = "$" + fmtNum(x.price, "USD");
      else if (x.label === "日経平均") val = fmtNum(x.price, "USD") + "円";
      else val = fmtNum(x.price, "USD");
      return (
        '<div class="market-chip"><span class="chip-label">' + esc(x.label) + "</span>" +
        '<span class="chip-value num">' + val + (x.stale ? " ⚠" : "") + "</span></div>"
      );
    }).join("");
    $("market").innerHTML =
      '<div class="section-title">Market</div><div class="market-row">' + html + "</div>" +
      '<div class="as-of" style="margin-top:6px">各指標は直近営業日の終値（⚠＝取得日が3日以上前）</div>';
  }

  function renderScriptBox(d) {
    if (!d.script_file) { $("scriptBox").innerHTML = ""; return; }
    $("scriptBox").innerHTML =
      '<details class="script-details"><summary>📄 今日の原稿を読む</summary>' +
      '<div class="script-text" id="scriptText">読み込み中...</div></details>';
    var loaded = false;
    $("scriptBox").querySelector("details").addEventListener("toggle", function () {
      if (loaded || !this.open) return;
      loaded = true;
      fetch(d.script_file + "?t=" + Date.now())
        .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.text(); })
        .then(function (t) { $("scriptText").textContent = t; })
        .catch(function (e) { $("scriptText").textContent = "原稿を読み込めませんでした: " + e.message; });
    });
  }

  function renderArchive(d) {
    var scripts = (d.recent_scripts || []).filter(function (s) { return s.date !== d.date; });
    if (!scripts.length) { $("archive").innerHTML = ""; return; }
    $("archive").innerHTML =
      '<details class="script-details"><summary>🗂 過去の原稿（' + scripts.length + "日分）</summary>" +
      '<div class="archive-list">' +
      scripts.map(function (s) {
        return '<a class="archive-link" href="' + esc(s.file) + '" target="_blank" rel="noopener">' + esc(s.date) + "</a>";
      }).join("") +
      "</div></details>";
  }

  function renderSummary(d) {
    var html = "";
    var totals = d.totals || {};
    ["JPY", "USD"].forEach(function (cur) {
      var t = totals[cur];
      if (!t) return;
      html +=
        '<div class="summary-card">' +
        '<div class="label">評価額合計（' + cur + "）</div>" +
        '<div class="value num">' + sym(cur) + fmtNum(t.market_value, cur) + "</div>" +
        '<div class="sub num ' + pnlClass(t.pnl_amount) + '">' +
        (t.pnl_amount >= 0 ? "+" : "") + fmtNum(t.pnl_amount, cur) + "</div>" +
        "</div>";
    });
    var wide = "";
    if (d.total_jpy != null) {
      wide =
        '<div class="summary-card wide">' +
        '<div class="label">総資産（円換算・投信除く）</div>' +
        '<div class="value num">¥' + d.total_jpy.toLocaleString("ja-JP") + "</div>" +
        '<div class="sub" style="color:var(--text-dim)">@' + d.usdjpy.toFixed(2) + "円/ドルで換算</div>" +
        "</div>";
    }
    $("summary").innerHTML = (html ? '<div class="summary-row">' + html + "</div>" : "") + wide;
  }

  function lineBox(label, cls, valueHtml, gapHtml) {
    return (
      '<div class="line-item"><span class="line-label">' + label + "</span>" +
      '<span class="line-value ' + cls + ' num">' + valueHtml + "</span>" +
      (gapHtml ? '<span class="line-gap num">' + gapHtml + "</span>" : "") +
      "</div>"
    );
  }

  function renderHoldings(d) {
    var html = "";
    (d.positions || []).forEach(function (p) {
      var s = sym(p.currency);
      if (p.price == null) {
        html +=
          '<div class="stock-card"><div class="card-header"><div>' +
          '<span class="stock-name">' + esc(p.name) + "</span>" +
          '<span class="ticker">' + esc(p.ticker) + "</span></div>" +
          '<span class="thesis-tag">' + esc(p.thesis) + "</span></div>" +
          '<div class="fund-note">株価取得失敗<span class="stale-badge">データ古い</span></div></div>';
        return;
      }
      var badges = "";
      if (p.stale) badges += '<span class="stale-badge">データ古い</span>';
      if (p.sl_alert) badges += '<span class="alert-badge">損切り接近</span>';
      if (p.tp_alert) badges += '<span class="alert-badge">利確接近</span>';

      var lines = "";
      if (p.stop_loss != null) {
        lines += lineBox("損切ライン", "sl", s + fmtNum(p.stop_loss, p.currency),
          "あと" + fmtNum(p.sl_gap_price, p.currency) + "・" + p.sl_gap_pct + "%下");
      } else {
        lines += lineBox("損切ライン", "", "—", "");
      }
      if (p.take_profit != null) {
        lines += lineBox("利確ライン", "tp", s + fmtNum(p.take_profit, p.currency),
          "あと" + fmtNum(p.tp_gap_price, p.currency) + "・" + p.tp_gap_pct + "%上");
      } else {
        lines += lineBox("利確ライン", "", "比率管理", "");
      }

      html +=
        '<div class="stock-card">' +
        '<div class="card-header"><div>' +
        '<span class="stock-name">' + esc(p.name) + "</span>" +
        '<span class="ticker">' + esc(p.ticker) + "</span></div>" +
        '<span class="thesis-tag">' + esc(p.thesis) + "</span></div>" +
        '<div class="card-body">' +
        '<div class="current-price num">' + s + fmtNum(p.price, p.currency) + "</div>" +
        '<div class="pnl num ' + pnlClass(p.pnl_pct) + '">' + signed(p.pnl_pct) + "%</div></div>" +
        '<div class="as-of">' + esc(p.as_of) + " 終値" + badges + "</div>" +
        '<div class="card-lines">' + lines + "</div>" +
        "</div>";
    });

    (d.funds || []).forEach(function (f) {
      html +=
        '<div class="stock-card fund-card">' +
        '<div class="card-header"><div>' +
        '<span class="stock-name">' + esc(f.name) + "</span>" +
        '<span class="ticker">投資信託</span></div>' +
        '<span class="thesis-tag">' + esc(f.thesis) + "</span></div>" +
        '<div class="fund-note">基準価額の自動取得対象外。' + esc(f.note) + "</div></div>";
    });

    (d.excluded || []).forEach(function (x) {
      html +=
        '<div class="stock-card excluded-card">' +
        '<div class="card-header"><div>' +
        '<span class="stock-name">' + esc(x.name) + "</span>" +
        '<span class="ticker">' + esc(x.ticker) + "</span></div>" +
        '<span class="thesis-tag">未購入・監視中</span></div>' +
        '<div class="fund-note">' + esc(x.note) + "</div></div>";
    });

    $("holdings").innerHTML = html || '<div class="fund-note">保有銘柄がありません</div>';
  }

  /* ===================== 設定画面（settings.html） ===================== */

  var state = { portfolio: null, sha: null, source: null, editingIndex: null };

  function getPat() { return localStorage.getItem(LS_PAT) || ""; }
  function getRepo() { return localStorage.getItem(LS_REPO) || REPO_DEFAULT; }

  function apiUrl() {
    return "https://api.github.com/repos/" + getRepo() + "/contents/portfolio.json";
  }
  function apiHeaders() {
    return {
      "Authorization": "Bearer " + getPat(),
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    };
  }
  function b64EncodeUtf8(str) {
    var bytes = new TextEncoder().encode(str);
    var bin = "";
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }
  function b64DecodeUtf8(b64) {
    var bin = atob(b64.replace(/\n/g, ""));
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }
  function nowJstIso() {
    var jst = new Date(Date.now() + 9 * 3600 * 1000);
    return jst.toISOString().replace(/\.\d{3}Z$/, "+09:00");
  }

  function setStatus(msg, ok) {
    var el = $("statusMsg");
    el.textContent = msg;
    el.className = "status-msg " + (ok ? "ok" : "err");
  }

  function loadSettingsPage() {
    $("repoInput").value = getRepo();
    $("patInput").value = getPat();
    updatePatStatus();
    loadPortfolioForEdit();

    $("savePatBtn").addEventListener("click", function () {
      var pat = $("patInput").value.trim();
      var repo = $("repoInput").value.trim() || REPO_DEFAULT;
      if (pat) localStorage.setItem(LS_PAT, pat); else localStorage.removeItem(LS_PAT);
      localStorage.setItem(LS_REPO, repo);
      updatePatStatus();
      setStatus(pat ? "トークンを保存しました（このブラウザのlocalStorageのみ）" : "トークンを削除しました", true);
      loadPortfolioForEdit();
    });
    $("clearPatBtn").addEventListener("click", function () {
      localStorage.removeItem(LS_PAT);
      $("patInput").value = "";
      updatePatStatus();
      setStatus("トークンを削除しました", true);
    });
    $("testPatBtn").addEventListener("click", function () {
      if (!getPat()) { setStatus("PATが未設定です", false); return; }
      setStatus("接続テスト中...", true);
      fetch(apiUrl(), { headers: apiHeaders() })
        .then(function (r) {
          if (r.ok) setStatus("接続OK：" + getRepo() + " の portfolio.json を読み取れました", true);
          else if (r.status === 401) setStatus("接続NG（401）：トークンが無効か期限切れです", false);
          else if (r.status === 403) setStatus("接続NG（403）：権限不足です。Contents: Read/Write を確認してください", false);
          else if (r.status === 404) setStatus("接続NG（404）：リポジトリ名の誤りか、PATにこのリポジトリへのアクセス権がありません", false);
          else setStatus("接続NG（HTTP " + r.status + "）", false);
        })
        .catch(function (e) { setStatus("接続NG: " + e.message, false); });
    });
    $("regenBtn").addEventListener("click", function () {
      if (!getPat()) { setStatus("再生成にはPATが必要です（Actions: Read/Write権限）", false); return; }
      if (!confirm("GitHub Actions を起動して配信を再生成しますか？（数分かかります）")) return;
      setStatus("Actions を起動中...", true);
      fetch("https://api.github.com/repos/" + getRepo() + "/actions/workflows/daily.yml/dispatches", {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({ ref: "main" }),
      })
        .then(function (r) {
          if (r.status === 204) {
            setStatus("再生成を開始しました。5〜10分後にこのページを再読み込みしてください", true);
          } else if (r.status === 403) {
            setStatus("起動できません（403）：PATに Actions: Read/Write 権限を追加してください", false);
          } else {
            return r.json().then(function (j) {
              setStatus("起動できません（HTTP " + r.status + "）: " + (j.message || ""), false);
            });
          }
        })
        .catch(function (e) { setStatus("起動できません: " + e.message, false); });
    });
    $("addBtn").addEventListener("click", function () { openForm(null); });
    $("cancelBtn").addEventListener("click", closeForm);
    $("formSaveBtn").addEventListener("click", submitForm);
    $("commitBtn").addEventListener("click", savePortfolio);
  }

  function updatePatStatus() {
    $("patStatus").textContent = getPat()
      ? "方式A：GitHub APIで直接コミットします（対象: " + getRepo() + "）"
      : "方式B：PAT未設定のため、保存時にコミット用JSONを表示します";
  }

  function loadPortfolioForEdit() {
    var done = function (pf, sha, source) {
      state.portfolio = pf;
      state.sha = sha;
      state.source = source;
      renderEditor();
    };
    if (getPat()) {
      fetch(apiUrl(), { headers: apiHeaders() })
        .then(function (r) { if (!r.ok) throw new Error("GitHub API HTTP " + r.status); return r.json(); })
        .then(function (j) { done(JSON.parse(b64DecodeUtf8(j.content)), j.sha, "GitHub API（最新）"); })
        .catch(function (e) {
          setStatus("GitHub APIから読み込めません（" + e.message + "）。サイト同梱のコピーを使用します", false);
          loadLocalCopy(done);
        });
    } else {
      loadLocalCopy(done);
    }
  }

  function loadLocalCopy(done) {
    fetch("portfolio.json?t=" + Date.now())
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (pf) { done(pf, null, "サイト同梱コピー（前回実行時点）"); })
      .catch(function (e) { setStatus("portfolio.json を読み込めません: " + e.message, false); });
  }

  function renderEditor() {
    var pf = state.portfolio;
    if (!pf) return;
    $("sourceNote").textContent = "データソース: " + state.source + "（last_updated: " + (pf.last_updated || "—") + "）";
    var html = "";
    pf.holdings.forEach(function (h, i) {
      var meta = [];
      if (h.is_fund) meta.push("投資信託");
      meta.push((h.shares || 0) + (h.is_fund ? "口" : "株") + " @ " + sym(h.currency) + fmtNum(h.cost, h.currency));
      if (h.stop_loss != null) meta.push("損切 " + sym(h.currency) + fmtNum(h.stop_loss, h.currency));
      if (h.take_profit != null) meta.push("利確 " + sym(h.currency) + fmtNum(h.take_profit, h.currency));
      html +=
        '<div class="stock-card">' +
        '<div class="card-header"><div>' +
        '<span class="stock-name">' + esc(h.name) + "</span>" +
        '<span class="ticker">' + esc(h.ticker || h.id) + "</span></div>" +
        '<span class="thesis-tag">' + esc(h.thesis) + "</span></div>" +
        '<div class="holding-meta num">' + esc(meta.join(" ／ ")) + "</div>" +
        (h.note ? '<div class="holding-meta">' + esc(h.note) + "</div>" : "") +
        '<div class="card-actions">' +
        '<button class="btn small" data-edit="' + i + '">編集</button>' +
        '<button class="btn small danger" data-del="' + i + '">削除</button>' +
        "</div></div>";
    });
    $("holdingsList").innerHTML = html;
    $("holdingsList").querySelectorAll("[data-edit]").forEach(function (b) {
      b.addEventListener("click", function () { openForm(parseInt(b.dataset.edit, 10)); });
    });
    $("holdingsList").querySelectorAll("[data-del]").forEach(function (b) {
      b.addEventListener("click", function () {
        var i = parseInt(b.dataset.del, 10);
        if (confirm("「" + pf.holdings[i].name + "」を削除しますか？\n（「保存（コミット）」を押すまで確定しません）")) {
          pf.holdings.splice(i, 1);
          renderEditor();
          setStatus("削除しました。「保存（コミット）」で確定してください", true);
        }
      });
    });
  }

  function openForm(index) {
    state.editingIndex = index;
    var h = index != null ? state.portfolio.holdings[index] : {
      id: "", ticker: "", name: "", thesis: THESES[0],
      shares: 0, cost: 0, currency: "JPY", stop_loss: null, take_profit: null, note: "",
    };
    $("formTitle").textContent = index != null ? "銘柄を編集" : "銘柄を追加";
    $("f_id").value = h.id || "";
    $("f_ticker").value = h.ticker || "";
    $("f_name").value = h.name || "";
    $("f_thesis").value = THESES.indexOf(h.thesis) >= 0 ? h.thesis : "その他";
    $("f_shares").value = h.shares != null ? h.shares : "";
    $("f_cost").value = h.cost != null ? h.cost : "";
    $("f_currency").value = h.currency || "JPY";
    $("f_stop_loss").value = h.stop_loss != null ? h.stop_loss : "";
    $("f_take_profit").value = h.take_profit != null ? h.take_profit : "";
    $("f_note").value = h.note || "";
    $("f_is_fund").checked = !!h.is_fund;
    $("formError").textContent = "";
    $("editForm").style.display = "block";
    $("editForm").scrollIntoView({ behavior: "smooth" });
  }

  function closeForm() {
    $("editForm").style.display = "none";
    state.editingIndex = null;
  }

  function validateTicker(ticker, isFund) {
    if (!ticker) return isFund ? null : "tickerは必須です（投資信託のみ空欄可）";
    if (/^[0-9A-Z]{4}\.T$/.test(ticker)) return null;       // 日本株: 4桁コード + .T
    if (/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker)) return null; // 米国株: 英字ティッカー
    return "ticker形式が不正です（日本株: 1234.T / 米国株: 英大文字）";
  }

  function numOrNull(v) {
    if (v === "" || v == null) return null;
    var n = Number(v);
    return isNaN(n) ? null : n;
  }

  function submitForm() {
    var isFund = $("f_is_fund").checked;
    var ticker = $("f_ticker").value.trim().toUpperCase();
    var h = {
      id: $("f_id").value.trim(),
      ticker: ticker || null,
      name: $("f_name").value.trim(),
      thesis: $("f_thesis").value,
      shares: numOrNull($("f_shares").value) || 0,
      cost: numOrNull($("f_cost").value) || 0,
      currency: $("f_currency").value,
      stop_loss: numOrNull($("f_stop_loss").value),
      take_profit: numOrNull($("f_take_profit").value),
      note: $("f_note").value.trim(),
    };
    if (isFund) h.is_fund = true;

    var err = null;
    if (!h.id) err = "idは必須です";
    else if (!h.name) err = "銘柄名は必須です";
    else err = validateTicker(ticker, isFund);
    if (!err && state.editingIndex == null &&
        state.portfolio.holdings.some(function (x) { return x.id === h.id; })) {
      err = "同じidの銘柄が既に存在します";
    }
    if (err) { $("formError").textContent = err; return; }

    if (state.editingIndex != null) {
      state.portfolio.holdings[state.editingIndex] = h;
    } else {
      state.portfolio.holdings.push(h);
    }
    closeForm();
    renderEditor();
    setStatus("変更しました。「保存（コミット）」で確定してください", true);
  }

  function savePortfolio() {
    if (!state.portfolio) return;
    state.portfolio.last_updated = nowJstIso();
    var jsonText = JSON.stringify(state.portfolio, null, 2) + "\n";

    if (!getPat()) {
      // 方式B：コミット用JSONを表示して手動コミット
      $("jsonOut").value = jsonText;
      $("jsonOutWrap").style.display = "block";
      setStatus("PAT未設定のため方式Bです。以下のJSONを portfolio.json として手動コミットしてください", true);
      return;
    }

    setStatus("GitHubへ保存中...", true);
    var doPut = function (sha) {
      var body = {
        message: "📊 portfolio.json をサイトから更新",
        content: b64EncodeUtf8(jsonText),
      };
      if (sha) body.sha = sha;
      return fetch(apiUrl(), {
        method: "PUT",
        headers: apiHeaders(),
        body: JSON.stringify(body),
      });
    };
    // 最新のshaを取得してから更新（競合防止）
    fetch(apiUrl(), { headers: apiHeaders() })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { return doPut(j ? j.sha : state.sha); })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (j) { throw new Error("HTTP " + r.status + ": " + (j.message || "")); });
        return r.json();
      })
      .then(function (j) {
        state.sha = j.content ? j.content.sha : null;
        $("jsonOutWrap").style.display = "none";
        setStatus("保存しました（コミット完了）。次回の自動実行で原稿・ダッシュボードに反映されます", true);
      })
      .catch(function (e) { setStatus("保存に失敗しました: " + e.message, false); });
  }

  /* ===================== 起動 ===================== */
  document.addEventListener("DOMContentLoaded", function () {
    if ($("holdings")) renderDashboard();
    if ($("holdingsList")) loadSettingsPage();
  });
})();
