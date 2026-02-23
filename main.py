import os
import feedparser
import yfinance as yf
import requests
from datetime import datetime, timedelta
from groq import Groq
import json

# ── 環境變數 ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
GROQ_API_KEY     = os.environ['GROQ_API_KEY']
FRED_API_KEY     = os.environ.get('FRED_API_KEY', '')  # 選填，沒有也能跑

groq_client = Groq(api_key=GROQ_API_KEY)

# ═══════════════════════════════════════════════════════════
# 1. 市場數據（yfinance，完全免費）
# ═══════════════════════════════════════════════════════════
def get_market_data():
    symbols = {
        '道瓊工業': '^DJI',
        '美股S&P500': '^GSPC',
        '那斯達克': '^IXIC',
        '費城半導體': '^SOX',
        '德國股市': '^GDAXI',
        '法國股市': '^FCHI',
        '英國股市': '^FTSE',
        'VT全球ETF': 'VT',
        'QQQ科技ETF': 'QQQ',
        'AI科技 (BOTZ)': 'BOTZ'
    }
    rows = []
    for name, sym in symbols.items():
        try:
            t    = yf.Ticker(sym)
            hist = t.history(period='5d')
            if len(hist) >= 2:
                price = hist['Close'].iloc[-1]
                prev  = hist['Close'].iloc[-2]
                pct   = (price - prev) / prev * 100
                vol   = hist['Volume'].iloc[-1]
                arrow = '▲' if pct >= 0 else '▼'
                color = '#22c55e' if pct >= 0 else '#ef4444'
                rows.append({
                    'name': name, 'price': f'{price:.2f}',
                    'pct': f'{pct:+.2f}%', 'arrow': arrow,
                    'color': color, 'vol': f'{vol/1e6:.1f}M'
                })
        except Exception as e:
            print(f'跳過 {sym}: {e}')
    return rows


# ═══════════════════════════════════════════════════════════
# 2. 總體經濟指標（FRED API，免費申請 fred.stlouisfed.org）
# ═══════════════════════════════════════════════════════════
def get_fred_data():
    if not FRED_API_KEY:
        return []
    indicators = {
        'DGS10':  '美國10年期公債殖利率',
        'DGS2':   '美國2年期公債殖利率',
        'FEDFUNDS':'聯邦基金利率',
        'NAPM':   '製造業PMI（ISM）',
    }
    results = []
    for series_id, label in indicators.items():
        try:
            url = 'https://api.stlouisfed.org/fred/series/observations'
            params = {
                'series_id': series_id,
                'api_key': FRED_API_KEY,
                'file_type': 'json',
                'limit': 2,
                'sort_order': 'desc'
            }
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            obs = data.get('observations', [])
            if obs:
                val  = obs[0]['value']
                prev = obs[1]['value'] if len(obs) > 1 else val
                try:
                    diff = float(val) - float(prev)
                    arrow = '▲' if diff > 0 else '▼' if diff < 0 else '─'
                    color = '#ef4444' if diff > 0 and 'DGS' in series_id else '#22c55e'
                    results.append({'label': label, 'val': val, 'arrow': arrow, 'color': color})
                except:
                    results.append({'label': label, 'val': val, 'arrow': '─', 'color': '#888'})
        except Exception as e:
            print(f'FRED {series_id} 失敗: {e}')
    # 殖利率曲線倒掛偵測
    try:
        dgs10 = next(x for x in results if '10年' in x['label'])
        dgs2  = next(x for x in results if '2年' in x['label'])
        spread = float(dgs10['val']) - float(dgs2['val'])
        status = '⚠️ 倒掛中（歷史衰退前兆）' if spread < 0 else '✓ 正常'
        results.append({'label': f'殖利率曲線（10Y-2Y）= {spread:.2f}%', 'val': status, 'arrow': '', 'color': '#ef4444' if spread < 0 else '#22c55e'})
    except:
        pass
    return results


# ═══════════════════════════════════════════════════════════
# 3. SEC EDGAR 最新重大文件（官方API，完全免費）
# ═══════════════════════════════════════════════════════════
def get_sec_filings():
    """追蹤NVIDIA、美光、台積電ADR等重要公司的最新SEC申報"""
    companies = {
        'NVDA': 'NVIDIA',
        'MU':   '美光科技',
        'AMAT': '應用材料',
        'LRCX': '科林研發',
        'AVGO': '博通',
    }
    filings = []
    headers = {'User-Agent': 'InvestBot research@example.com'}
    for ticker, name in companies.items():
        try:
            # 先查CIK
            r = requests.get(
                f'https://efts.sec.gov/LATEST/search-index?q="{ticker}"&dateRange=custom&startdt={(datetime.now()-timedelta(days=3)).strftime("%Y-%m-%d")}&forms=8-K',
                headers=headers, timeout=10
            )
            data = r.json()
            hits = data.get('hits', {}).get('hits', [])
            for hit in hits[:2]:
                src = hit.get('_source', {})
                filings.append({
                    'company': name,
                    'form': src.get('form_type', '8-K'),
                    'title': src.get('display_names', ticker),
                    'date': src.get('file_date', ''),
                    'url': f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=8-K&dateb=&owner=include&count=5"
                })
        except Exception as e:
            print(f'SEC {ticker} 失敗: {e}')
    return filings[:6]


# ═══════════════════════════════════════════════════════════
# 4. 免費RSS新聞（可靠來源）
# ═══════════════════════════════════════════════════════════
def get_news():
    feeds = [
        ('Yahoo科技', 'https://finance.yahoo.com/rss/topstories'),
        ('Google財經-半導體', 'https://news.google.com/rss/search?q=semiconductor+MLCC+AI+server&hl=en&gl=US&ceid=US:en'),
        ('Google財經-台股',   'https://news.google.com/rss/search?q=TSMC+Yageo+passive+components&hl=en&gl=US&ceid=US:en'),
        ('Seeking Alpha',     'https://seekingalpha.com/feed.xml'),
    ]
    news_items = []
    for source, url in feeds:
        try:
            feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
            for entry in feed.entries[:4]:
                title = entry.get('title', '').strip()
                link  = entry.get('link', '')
                pub   = entry.get('published', '')[:10] if entry.get('published') else ''
                if title and len(title) > 10:
                    news_items.append({'source': source, 'title': title, 'link': link, 'date': pub})
            if len(news_items) >= 12:
                break
        except Exception as e:
            print(f'{source} RSS失敗: {e}')
    return news_items[:12]


# ═══════════════════════════════════════════════════════════
# 5. 推薦追蹤的分析師資源（靜態清單，每次都顯示）
# ═══════════════════════════════════════════════════════════
def get_analysts():
    return [
        {
            'name': 'Dylan Patel @SemiAnalysis',
            'platform': 'X / Substack',
            'focus': '半導體供應鏈深度分析，AI晶片成本結構',
            'url': 'https://semianalysis.com',
            'why': '最接近機構水準的免費半導體研究，每篇都值得讀'
        },
        {
            'name': 'Chip Stock Investor @ChipStockInvest',
            'platform': 'X',
            'focus': '半導體個股、被動元件、供應鏈追蹤',
            'url': 'https://x.com/ChipStockInvest',
            'why': '台灣半導體供應鏈相關資訊整理最即時'
        },
        {
            'name': 'TrendForce 最新報告',
            'platform': 'Web RSS',
            'focus': 'DRAM/NAND/Server/MLCC市場報告摘要',
            'url': 'https://www.trendforce.com/news/',
            'why': '每篇摘要免費，點進去看標題+導言已經夠用'
        },
        {
            'name': 'SEMI B/B Ratio 月報',
            'platform': 'semi.org',
            'focus': '北美半導體設備出貨/訂單比值，景氣領先指標',
            'url': 'https://www.semi.org/en/products-services/market-data/book-to-bill',
            'why': 'B/B > 1 = 景氣向上，< 1 = 下行，每月必看'
        },
        {
            'name': 'Murata IR 季報',
            'platform': '官方IR頁面',
            'focus': 'MLCC全球最大廠商，法說會展望是被動元件最權威訊號',
            'url': 'https://corporate.murata.com/en-us/ir/library/presentation',
            'why': 'Murata怎麼說，MLCC市場就怎麼走'
        },
        {
            'name': 'EarningsCall.biz',
            'platform': 'Web / RSS',
            'focus': 'NVIDIA、台積電、美光等法說會完整逐字稿',
            'url': 'https://earningscall.biz',
            'why': '免費法說會逐字稿，貼給Claude分析比看摘要深入10倍'
        },
    ]


# ═══════════════════════════════════════════════════════════
# 6. Groq AI 分析（根據所有數據生成）
# ═══════════════════════════════════════════════════════════
def generate_analysis(market_rows, macro_data, news_items):
    today = datetime.now().strftime('%Y/%m/%d')

    market_text = '\n'.join([f"{r['name']}: {r['price']} {r['pct']}" for r in market_rows])
    macro_text  = '\n'.join([f"{m['label']}: {m['val']}" for m in macro_data]) if macro_data else '（未設定FRED API Key）'
    news_text   = '\n'.join([f"• {n['title']}" for n in news_items[:8]])

    prompt = f"""今天是 {today}。請用繁體中文回答，語氣像懂投資的朋友直接說重點。

【今日市場】
{market_text}

【總體經濟指標】
{macro_text}

【今日新聞標題】
{news_text}

請依序回答三段，每段2-3句話，不要廢話：

第一段【市場今天在說什麼】：
漲跌的主因是什麼？哪個最強哪個最弱？半導體和AI相關ETF的表現說明了什麼？

第二段【總體環境怎麼樣】：
利率和債券數據透露什麼訊號？殖利率曲線現在是什麼狀況？對長期ETF投資者意味著什麼？

第三段【本週值得注意的事】：
從新聞和數據看，有沒有需要留意的趨勢或風險？對持有VT、QQQ、SOXX、台灣50的人有什麼影響？

最後一行固定寫：「以上是資訊整理，不是買賣建議。」"""

    try:
        response = groq_client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[
                {'role': 'system', 'content': '你是一個有十年經驗的投資研究員，說話簡潔有重點，只說有數據支撐的事，不說「我無法預測」這類廢話。'},
                {'role': 'user', 'content': prompt}
            ],
            max_tokens=600,
            temperature=0.6
        )
        return response.choices[0].message.content
    except Exception as e:
        return f'AI分析失敗：{e}'


# ═══════════════════════════════════════════════════════════
# 7. 生成 HTML 報告（輸出到 docs/index.html）
# ═══════════════════════════════════════════════════════════
def generate_html(market_rows, macro_data, news_items, analysts, analysis_text, sec_filings):
    today     = datetime.now().strftime('%Y年%m月%d日')
    weekday   = ['週一','週二','週三','週四','週五','週六','週日'][datetime.now().weekday()]
    timestamp = datetime.now().strftime('%H:%M UTC')

    # 市場表格行
    market_html = ''
    for r in market_rows:
        market_html += f'''
        <tr>
          <td>{r["name"]}</td>
          <td style="font-family:'IBM Plex Mono',monospace;">{r["price"]}</td>
          <td style="color:{r["color"]};font-weight:600;font-family:'IBM Plex Mono',monospace;">{r["arrow"]}{r["pct"]}</td>
        </tr>'''

    # 總經行
    macro_html = ''
    if macro_data:
        for m in macro_data:
            macro_html += f'<div class="macro-row"><span>{m["label"]}</span><span style="color:{m["color"]};font-family:\'IBM Plex Mono\',monospace;">{m["arrow"]} {m["val"]}</span></div>'
    else:
        macro_html = '<div class="macro-row" style="color:#666;">設定 FRED_API_KEY 後可顯示（免費申請：fred.stlouisfed.org）</div>'

    # 新聞行
    news_html = ''
    for n in news_items:
        link = f'href="{n["link"]}"' if n["link"] else ''
        news_html += f'<div class="news-item"><span class="news-source">{n["source"]}</span><a {link} target="_blank" class="news-title">{n["title"]}</a></div>'

    # 分析師清單
    analysts_html = ''
    for a in analysts:
        analysts_html += f'''
        <div class="analyst-card">
          <div class="analyst-top">
            <span class="analyst-name">{a["name"]}</span>
            <span class="analyst-platform">{a["platform"]}</span>
          </div>
          <div class="analyst-focus">{a["focus"]}</div>
          <div class="analyst-why">→ {a["why"]}</div>
          <a href="{a["url"]}" target="_blank" class="analyst-link">{a["url"]}</a>
        </div>'''

    # SEC申報
    sec_html = ''
    if sec_filings:
        for f in sec_filings:
            sec_html += f'<div class="sec-item"><span class="sec-tag">{f["form"]}</span><span class="sec-company">{f["company"]}</span><a href="{f["url"]}" target="_blank" class="sec-title">{f["title"]}</a><span class="sec-date">{f["date"]}</span></div>'
    else:
        sec_html = '<div style="color:#666;font-size:13px;">今日無重大SEC申報</div>'

    # AI分析（段落處理）
    analysis_html = ''
    for para in analysis_text.split('\n'):
        if para.strip():
            analysis_html += f'<p>{para.strip()}</p>'

    return f'''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="3600">
<title>投資情報日報 · {today}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+TC:wght@300;400;500;600;700&family=Noto+Serif+TC:wght@700;900&display=swap" rel="stylesheet">
<style>
:root{{
  --bg:#0f1117;--bg2:#161b27;--bg3:#1e2535;
  --border:#2a3347;--text:#d4dbe8;--text2:#7a8499;
  --green:#22c55e;--amber:#f59e0b;--blue:#3b82f6;
  --red:#ef4444;--purple:#a78bfa;
}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:"Noto Sans TC",sans-serif;font-size:14px;line-height:1.7;}}
a{{color:var(--blue);text-decoration:none;}}
a:hover{{text-decoration:underline;}}

.header{{background:linear-gradient(135deg,#1a2744,#0f1117);border-bottom:1px solid var(--border);padding:32px 24px;text-align:center;}}
.header-date{{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--amber);letter-spacing:3px;margin-bottom:8px;}}
.header-title{{font-family:"Noto Serif TC",serif;font-size:clamp(20px,4vw,34px);font-weight:900;color:#fff;margin-bottom:6px;}}
.header-sub{{font-size:12px;color:var(--text2);font-family:"IBM Plex Mono",monospace;}}

.nav{{display:flex;justify-content:center;gap:8px;flex-wrap:wrap;padding:14px 24px;background:var(--bg2);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:50;}}
.nav a{{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--text2);padding:5px 12px;border:1px solid var(--border);border-radius:20px;transition:all .2s;}}
.nav a:hover{{color:var(--amber);border-color:var(--amber);text-decoration:none;}}

.main{{max-width:900px;margin:0 auto;padding:32px 24px;display:grid;grid-template-columns:1fr 1fr;gap:24px;}}
.full{{grid-column:1/-1;}}
@media(max-width:640px){{.main{{grid-template-columns:1fr;}}.full{{grid-column:1;}}}}

.card{{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:20px;}}
.card-title{{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--amber);letter-spacing:2px;text-transform:uppercase;margin-bottom:14px;display:flex;align-items:center;gap:8px;}}
.card-title::before{{content:"▸";}}

table{{width:100%;border-collapse:collapse;}}
th{{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--text2);padding:6px 8px;text-align:left;border-bottom:1px solid var(--border);letter-spacing:1px;}}
td{{padding:8px;border-bottom:1px solid rgba(42,51,71,.5);font-size:13px;}}
tr:last-child td{{border-bottom:none;}}
tr:hover td{{background:rgba(245,158,11,.04);}}

.macro-row{{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(42,51,71,.5);font-size:13px;}}
.macro-row:last-child{{border-bottom:none;}}

.news-item{{padding:10px 0;border-bottom:1px solid rgba(42,51,71,.4);}}
.news-item:last-child{{border-bottom:none;}}
.news-source{{font-family:"IBM Plex Mono",monospace;font-size:9px;color:var(--text2);background:var(--bg3);padding:2px 7px;border-radius:10px;margin-right:8px;letter-spacing:1px;}}
.news-title{{font-size:13px;color:var(--text);display:inline;}}
.news-title:hover{{color:var(--amber);}}

.ai-analysis p{{margin-bottom:14px;line-height:1.9;color:var(--text);}}
.ai-analysis p:last-child{{margin-bottom:0;color:var(--text2);font-size:12px;}}

.analyst-card{{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:14px;margin-bottom:10px;}}
.analyst-card:last-child{{margin-bottom:0;}}
.analyst-top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;flex-wrap:wrap;gap:4px;}}
.analyst-name{{font-size:13px;font-weight:600;color:#fff;}}
.analyst-platform{{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--blue);background:rgba(59,130,246,.1);padding:2px 8px;border-radius:10px;}}
.analyst-focus{{font-size:12px;color:var(--text);margin-bottom:3px;}}
.analyst-why{{font-size:12px;color:var(--green);margin-bottom:5px;}}
.analyst-link{{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--text2);}}

.sec-item{{display:flex;align-items:center;gap:8px;padding:9px 0;border-bottom:1px solid rgba(42,51,71,.4);flex-wrap:wrap;}}
.sec-item:last-child{{border-bottom:none;}}
.sec-tag{{font-family:"IBM Plex Mono",monospace;font-size:10px;background:rgba(167,139,250,.12);color:var(--purple);padding:2px 8px;border-radius:3px;white-space:nowrap;}}
.sec-company{{font-size:12px;color:var(--amber);font-weight:600;white-space:nowrap;}}
.sec-title{{font-size:12px;color:var(--text);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.sec-date{{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--text2);white-space:nowrap;}}

.footer{{text-align:center;padding:32px 24px;font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--text2);border-top:1px solid var(--border);margin-top:24px;}}
.update-badge{{display:inline-block;background:rgba(34,197,94,.1);color:var(--green);border:1px solid rgba(34,197,94,.25);padding:4px 12px;border-radius:20px;font-size:10px;letter-spacing:1px;}}
</style>
</head>
<body>

<div class="header">
  <div class="header-date">▸ {today} {weekday} · 更新於 {timestamp}</div>
  <div class="header-title">📊 每日投資情報日報</div>
  <div class="header-sub">市場數據 · 總體經濟 · 產業新聞 · SEC申報 · AI分析整理</div>
</div>

<div class="nav">
  <a href="#market">市場快照</a>
  <a href="#macro">總體經濟</a>
  <a href="#analysis">AI分析</a>
  <a href="#news">產業新聞</a>
  <a href="#sec">SEC申報</a>
  <a href="#analysts">追蹤資源</a>
</div>

<div class="main">

  <!-- 市場快照 -->
  <div class="card" id="market">
    <div class="card-title">市場快照</div>
    <table>
      <thead><tr><th>標的</th><th>價格</th><th>漲跌</th></tr></thead>
      <tbody>{market_html}</tbody>
    </table>
  </div>

  <!-- 總體經濟 -->
  <div class="card" id="macro">
    <div class="card-title">總體經濟指標</div>
    {macro_html}
  </div>

  <!-- AI分析 -->
  <div class="card full" id="analysis">
    <div class="card-title">🤖 今日AI分析（Llama 3.3 70B）</div>
    <div class="ai-analysis">{analysis_html}</div>
  </div>

  <!-- 產業新聞 -->
  <div class="card full" id="news">
    <div class="card-title">產業新聞（AI/半導體/被動元件）</div>
    {news_html}
  </div>

  <!-- SEC申報 -->
  <div class="card" id="sec">
    <div class="card-title">SEC重大申報（近3日）</div>
    {sec_html}
  </div>

  <!-- 分析師資源 -->
  <div class="card" id="analysts">
    <div class="card-title">每週必看：免費巨人肩膀</div>
    {analysts_html}
  </div>

</div>

<div class="footer">
  <div class="update-badge">● 每個交易日早上 09:00 自動更新</div><br><br>
  資訊整理僅供參考 · 不構成投資建議 · 投資一定有風險<br>
  數據來源：yfinance · FRED · SEC EDGAR · RSS · Groq Llama 3.3
</div>

</body>
</html>'''


# ═══════════════════════════════════════════════════════════
# 8. 儲存HTML + 發Telegram通知
# ═══════════════════════════════════════════════════════════
def save_html(html_content):
    os.makedirs('docs', exist_ok=True)
    with open('docs/index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    print('✓ HTML已儲存到 docs/index.html')


def send_telegram(market_rows, github_user, repo_name):
    today = datetime.now().strftime('%Y/%m/%d')
    page_url = f'https://{github_user}.github.io/{repo_name}/'

    # 找出漲最多和跌最多
    try:
        best  = max(market_rows, key=lambda x: float(x['pct'].replace('%','').replace('+','')))
        worst = min(market_rows, key=lambda x: float(x['pct'].replace('%','').replace('+','')))
        highlight = f"最強：{best['name']} {best['pct']}\n最弱：{worst['name']} {worst['pct']}"
    except:
        highlight = ''

    msg = (
        f"📊 <b>投資日報已更新 {today}</b>\n\n"
        f"{highlight}\n\n"
        f"🔗 <a href='{page_url}'>點這裡看完整報告</a>\n\n"
        f"<i>包含：市場快照 · 總體指標 · AI分析 · 產業新聞 · SEC申報</i>"
    )

    url  = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    data = {'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML', 'disable_web_page_preview': False}
    r    = requests.post(url, data=data, timeout=30)
    if r.status_code == 200:
        print('✓ Telegram通知已發送')
    else:
        print(f'✗ Telegram失敗：{r.text}')


# ═══════════════════════════════════════════════════════════
# 主程式
# ═══════════════════════════════════════════════════════════
def main():
    # 從環境變數讀取GitHub資訊（在workflow設定）
    github_user = os.environ.get('GITHUB_USER', 'your-username')
    repo_name   = os.environ.get('REPO_NAME', 'daily-invest-bot')

    print('1. 取得市場數據...')
    market_rows = get_market_data()

    print('2. 取得總體經濟指標...')
    macro_data = get_fred_data()

    print('3. 取得新聞...')
    news_items = get_news()

    print('4. 取得SEC申報...')
    sec_filings = get_sec_filings()

    print('5. 取得分析師清單...')
    analysts = get_analysts()

    print('6. 生成AI分析...')
    analysis = generate_analysis(market_rows, macro_data, news_items)

    print('7. 生成HTML頁面...')
    html = generate_html(market_rows, macro_data, news_items, analysts, analysis, sec_filings)
    save_html(html)

    print('8. 發送Telegram通知...')
    send_telegram(market_rows, github_user, repo_name)

    print('✓ 完成！')


if __name__ == '__main__':
    main()
