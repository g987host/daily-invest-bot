import os
import feedparser
import yfinance as yf
import requests
from datetime import datetime, timedelta, timezone
from groq import Groq

# ── 環境變數 ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
GROQ_API_KEY     = os.environ['GROQ_API_KEY']
FRED_API_KEY     = os.environ.get('FRED_API_KEY', '')

groq_client = Groq(api_key=GROQ_API_KEY)


# ═══════════════════════════════════════════════════════════
# 1. 市場數據
# ═══════════════════════════════════════════════════════════
def get_market_data():
    from datetime import date, timedelta
    symbols = {
        '道瓊工業': '^DJI',
        '美股S&P500': '^GSPC',
        '那斯達克100': '^NDX',
        '費城半導體': '^SOX',
        '德國股市': '^GDAXI',
        '法國股市': '^FCHI',
        '英國股市': '^FTSE',
        '台積電ADR':'TSM',
        '國際油價':'CL=F',
        'VIX':'^VIX',
        'VT全球ETF': 'VT',
        'QQQ科技ETF': 'QQQ',
        '半導體ETF': 'SOXX',
        'AI科技 (BOTZ)': 'BOTZ'
    }
    rows = []
    # ✅ 用明確日期取代 period，往前抓 7 天確保有足夠資料
    end   = date.today() + timedelta(days=1)  # +1 確保今天資料不被截掉
    start = date.today() - timedelta(days=7)
    for name, sym in symbols.items():
        try:
            eu_symbols = {'^GDAXI', '^FCHI', '^FTSE'}
            t    = yf.Ticker(sym)
            hist = t.history(start=start, end=end)            
            if len(hist) >= 2:
                price = hist['Close'].iloc[-1]
                prev  = hist['Close'].iloc[-2]
                if sym in eu_symbols:
                    price = t.fast_info['last_price']
                    prev  = hist['Close'].iloc[-1]  # 用 hist 最新的當 prev
                pct   = (price - prev) / prev * 100
                arrow = '▲' if pct >= 0 else '▼'
                color = '#22c55e' if pct >= 0 else '#ef4444'
                rows.append({
                    'name': name, 'price': f'{price:.2f}',
                    'pct': f'{pct:+.2f}%', 'arrow': arrow, 'color': color,
                    'raw_pct': pct
                })               
            elif len(hist) == 1:
                price = hist['Close'].iloc[-1]
                rows.append({
                    'name': name, 'price': f'{price:.2f}',
                    'pct': 'N/A', 'arrow': '-', 'color': '#888888',
                    'raw_pct': 0
                })
            # ✅ 印出實際日期，方便確認拿到的是哪幾天
            #print(f'  [{sym}] 最後兩筆: {hist.index[-2].date()} / {hist.index[-1].date()}')
        except Exception as e:
            print(f'跳過 {sym}: {e}')
    return rows


# ═══════════════════════════════════════════════════════════
# 2. 總體經濟（FRED）
# ═══════════════════════════════════════════════════════════
def get_fred_data():
    if not FRED_API_KEY:
        return []
    indicators = {
        'DGS10':   '美國10年期公債殖利率',
        'DGS2':    '美國2年期公債殖利率',
        'FEDFUNDS': '聯邦基金利率',
        'NAPM':    '製造業PMI（ISM）',
    }
    results = []
    for series_id, label in indicators.items():
        try:
            r = requests.get(
                'https://api.stlouisfed.org/fred/series/observations',
                params={'series_id': series_id, 'api_key': FRED_API_KEY,
                        'file_type': 'json', 'limit': 2, 'sort_order': 'desc'},
                timeout=10
            )
            obs = r.json().get('observations', [])
            if obs:
                val  = obs[0]['value']
                prev = obs[1]['value'] if len(obs) > 1 else val
                try:
                    diff  = float(val) - float(prev)
                    arrow = '▲' if diff > 0 else '▼' if diff < 0 else '─'
                    color = '#ef4444' if (diff > 0 and 'DGS' in series_id) else '#22c55e'
                    results.append({'label': label, 'val': val, 'arrow': arrow, 'color': color})
                except:
                    results.append({'label': label, 'val': val, 'arrow': '─', 'color': '#888'})
        except Exception as e:
            print(f'FRED {series_id}: {e}')

    # 殖利率曲線倒掛偵測
    try:
        v10 = float(next(x['val'] for x in results if '10年' in x['label']))
        v2  = float(next(x['val'] for x in results if '2年'  in x['label']))
        spread = v10 - v2
        status = f'{"⚠️ 倒掛中（衰退前兆）" if spread < 0 else "✓ 正常"}'
        color  = '#ef4444' if spread < 0 else '#22c55e'
        results.append({'label': f'殖利率曲線 10Y-2Y = {spread:.2f}%', 'val': status, 'arrow': '', 'color': color})
    except:
        pass
    return results


# ═══════════════════════════════════════════════════════════
# 3. 台股三大法人買賣超（TWSE 官方 API）
# ═══════════════════════════════════════════════════════════
def get_twse_institutional():
    """
    抓取台股三大法人整體買賣超資料（前一個交易日）
    來源：台灣證券交易所 BFI82U API（免費，無需 API Key）
    回傳 dict 包含：外資、投信、自營商、合計的買賣超金額（億元）
    """
    # 嘗試最近幾天，避開假日
    results = {}
    for days_back in range(0, 6):
        target = (datetime.now() - timedelta(days=days_back)).strftime('%Y%m%d')
        try:
            r = requests.get(
                'https://www.twse.com.tw/rwd/zh/fund/BFI82U',
                params={'response': 'json', 'dayDate': target, 'type': 'day'},
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=10
            )
            data = r.json()
            if data.get('stat') != 'OK' or not data.get('data'):
                continue

            date_str = data.get('date', target)
            rows = data['data']

            # debug：印出所有欄位名稱，方便排查
            print(f'  TWSE 欄位名稱: {[row[0].strip() for row in rows]}')

            parsed = {}
            for row in rows:
                name = row[0].strip()
                try:
                    # 買進、賣出、差額（單位：元）
                    buy  = int(row[1].replace(',', ''))
                    sell = int(row[2].replace(',', ''))
                    net  = int(row[3].replace(',', '').replace('+', ''))
                except:
                    continue

                # 用關鍵字比對，兼容各種名稱格式
                if ('外資及陸資(不含外資自營商)' in name or 'Foreign' in name) and '合計' not in name:
                    parsed['外資'] = {'buy': buy, 'sell': sell, 'net': net, 'label': '外資及陸資'}
                elif '投信' in name and '合計' not in name:
                    parsed['投信'] = {'buy': buy, 'sell': sell, 'net': net, 'label': '投信'}
                elif '自營' in name and '自行' in name:
                    parsed['自營(自行)'] = {'buy': buy, 'sell': sell, 'net': net}
                elif '自營' in name and '避險' in name:
                    parsed['自營(避險)'] = {'buy': buy, 'sell': sell, 'net': net}
                elif '三大法人' in name or ('合計' in name and '自營' not in name and '外資' not in name):
                    parsed['三大合計'] = {'buy': buy, 'sell': sell, 'net': net, 'label': '三大法人合計'}

            if parsed:
                # 計算自營商合計（自行+避險）
                try:
                    sb = parsed.get('自營(自行)', {})
                    sh = parsed.get('自營(避險)', {})
                    if sb or sh:
                        parsed['自營合計'] = {
                            'buy':   sb.get('buy', 0)  + sh.get('buy', 0),
                            'sell':  sb.get('sell', 0) + sh.get('sell', 0),
                            'net':   sb.get('net', 0)  + sh.get('net', 0),
                            'label': '自營商'
                        }
                except:
                    pass

                print(f'  解析結果: {list(parsed.keys())}')
                results['date'] = date_str
                results['data'] = parsed
                print(f'✓ 三大法人資料日期：{date_str}')
                return results

        except Exception as e:
            print(f'三大法人 {target} 失敗: {e}')
            continue

    print('⚠ 三大法人：無法取得資料')
    return {}


def fmt_yi(val):
    """將元單位轉成億元，加上方向符號（TWSE API 單位為元）"""
    yi = val / 100_000_000  # TWSE 單位是元，除以1億
    arrow = '▲' if yi > 0 else '▼' if yi < 0 else '─'
    color = '#22c55e' if yi > 0 else '#ef4444' if yi < 0 else '#888'
    return arrow, f'{yi:+.2f}億', color


# ═══════════════════════════════════════════════════════════
# 4. 新聞抓取（AI / 半導體 / 總體經濟）
# ═══════════════════════════════════════════════════════════
def get_news():
    feeds = [
        ('Google財經-AI',      'https://news.google.com/rss/search?q=AI+artificial+intelligence+chip+semiconductor&hl=en&gl=US&ceid=US:en'),
        ('Google財經-半導體',  'https://news.google.com/rss/search?q=NVIDIA+TSMC+semiconductor+earnings&hl=en&gl=US&ceid=US:en'),
        ('Google財經-總經',    'https://news.google.com/rss/search?q=Fed+interest+rate+inflation+economy&hl=en&gl=US&ceid=US:en'),
        ('Yahoo財經',          'https://finance.yahoo.com/rss/topstories'),
    ]
    items = []
    seen  = set()
    for source, url in feeds:
        try:
            feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
            for entry in feed.entries[:5]:
                title = entry.get('title', '').strip()
                link  = entry.get('link', '')
                if title and title not in seen and len(title) > 15:
                    seen.add(title)
                    items.append({'source': source, 'title_en': title, 'link': link, 'title_zh': ''})
        except Exception as e:
            print(f'{source} 失敗: {e}')
        if len(items) >= 16:
            break
    return items[:14]


# ═══════════════════════════════════════════════════════════
# 5. 用Groq批次翻譯新聞標題
# ═══════════════════════════════════════════════════════════
def translate_news(news_items):
    if not news_items:
        return news_items
    titles = '\n'.join([f'{i+1}. {n["title_en"]}' for i, n in enumerate(news_items)])
    prompt = f"""把以下英文財經新聞標題翻譯成繁體中文，每行一個，保留原本的編號格式，只回傳翻譯結果，不要任何解釋：

{titles}"""
    try:
        resp = groq_client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=800,
            temperature=0.3
        )
        lines = resp.choices[0].message.content.strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 解析 "1. 標題" 格式
            for i, item in enumerate(news_items):
                prefix = f'{i+1}.'
                if line.startswith(prefix):
                    zh = line[len(prefix):].strip()
                    if zh:
                        news_items[i]['title_zh'] = zh
    except Exception as e:
        print(f'翻譯失敗: {e}')
    return news_items


# ═══════════════════════════════════════════════════════════
# 6. 自動抓取「巨人肩膀」重點內容
# ═══════════════════════════════════════════════════════════
def get_giant_summaries():
    """
    抓取 TrendForce、SemiAnalysis、Seeking Alpha 等免費摘要，
    用AI整理成重點，直接顯示在頁面上
    """
    sources = [
        {
            'name': 'TrendForce',
            'url': 'https://www.trendforce.com/news/',
            'rss': 'https://www.trendforce.com/feed/',
            'focus': 'DRAM/Server/AI晶片市場報告'
        },
        {
            'name': 'SemiAnalysis',
            'url': 'https://semianalysis.com',
            'rss': 'https://semianalysis.com/feed/',
            'focus': 'AI晶片成本結構、供應鏈深度分析'
        },
        {
            'name': 'Seeking Alpha - 半導體',
            'url': 'https://seekingalpha.com/market-news/semiconductors',
            'rss': 'https://seekingalpha.com/tag/semiconductor-stocks.xml',
            'focus': '半導體個股分析、法說會摘要'
        },
        {
            'name': 'Digitimes Research',
            'url': 'https://www.digitimes.com',
            'rss': 'https://www.digitimes.com/rss/news.rss',
            'focus': '台灣科技供應鏈、伺服器市場'
        },
    ]

    all_content = []
    for src in sources:
        try:
            feed = feedparser.parse(src['rss'], request_headers={'User-Agent': 'Mozilla/5.0'})
            entries = []
            for entry in feed.entries[:4]:
                title   = entry.get('title', '').strip()
                summary = entry.get('summary', entry.get('description', '')).strip()
                # 清除HTML標籤（簡單處理）
                import re
                summary = re.sub(r'<[^>]+>', '', summary)[:300]
                if title:
                    entries.append(f'- {title}' + (f'：{summary}' if summary else ''))
            if entries:
                all_content.append({
                    'name': src['name'],
                    'focus': src['focus'],
                    'url': src['url'],
                    'raw': '\n'.join(entries),
                    'summary': ''
                })
                print(f'✓ {src["name"]} 抓到 {len(entries)} 則')
        except Exception as e:
            print(f'{src["name"]} RSS失敗: {e}')
            all_content.append({
                'name': src['name'],
                'focus': src['focus'],
                'url': src['url'],
                'raw': '',
                'summary': f'今日無法取得內容，請直接前往 {src["url"]}'
            })

    # 用Groq整理每個來源的重點
    for src in all_content:
        if not src['raw']:
            continue
        try:
            prompt = f"""以下是來自「{src["name"]}」（{src["focus"]}）的最新文章標題和摘要：

{src["raw"]}

請用繁體中文，用2-4句話整理出今天這個來源最值得注意的重點是什麼。
語氣簡潔直接，說具體的內容（有哪些公司、什麼趨勢、什麼數字），不要說「這個來源報導了...」這類廢話。
如果內容不夠具體就直接說「今日無特別重大消息」。"""

            resp = groq_client.chat.completions.create(
                model='llama-3.3-70b-versatile',
                messages=[{'role': 'user', 'content': prompt}],
                max_tokens=200,
                temperature=0.5
            )
            src['summary'] = resp.choices[0].message.content.strip()
        except Exception as e:
            src['summary'] = '摘要生成失敗'
            print(f'{src["name"]} 摘要失敗: {e}')

    return all_content


# ═══════════════════════════════════════════════════════════
# 7. AI主分析
# ═══════════════════════════════════════════════════════════
def generate_analysis(market_rows, macro_data, news_items, institutional):
    today = datetime.now().strftime('%Y/%m/%d')

    market_text = '\n'.join([f'{r["name"]}: {r["price"]} {r["pct"]}' for r in market_rows])
    macro_text  = '\n'.join([f'{m["label"]}: {m["val"]}' for m in macro_data]) if macro_data else '（未設定FRED API）'
    news_text   = '\n'.join([
        f'• {n["title_zh"] or n["title_en"]}'
        for n in news_items[:8]
    ])

    # 三大法人文字摘要
    inst_text = '（無資料）'
    if institutional.get('data'):
        d = institutional['data']
        date_str = institutional.get('date', '')
        lines = [f'台股三大法人 ({date_str})：']
        for key in ['外資', '投信', '自營合計', '三大合計']:
            if key in d:
                _, amt, _ = fmt_yi(d[key]['net'])
                lines.append(f'  {key}：{amt}')
        inst_text = '\n'.join(lines)

    prompt = f"""今天是 {today}。請用繁體中文回答，語氣像懂投資的朋友說重點。

【今日市場】
{market_text}

【總體經濟】
{macro_text}

【台股三大法人動向】
{inst_text}

【今日重要新聞】
{news_text}

請分四段回答，每段2-3句，不要廢話不要條列：

第一段【今天市場在說什麼】：漲跌主因？AI和半導體ETF透露什麼訊號？

第二段【台股籌碼解讀】：外資、投信方向一致還是對作？對後市有何暗示？

第三段【總體環境怎樣】：利率和債券數據的含義？對長期ETF投資者意味著什麼？

第四段【本週需要注意】：有沒有值得留意的趨勢或風險？對持有VT、QQQ、SOXX、台灣50的人有什麼影響？

最後一行固定寫：「以上是資訊整理，不是買賣建議。」"""

    try:
        resp = groq_client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[
                {'role': 'system', 'content': '你是有十年經驗的投資研究員，說話簡潔有重點，只說有數據支撐的事。'},
                {'role': 'user',   'content': prompt}
            ],
            max_tokens=600,
            temperature=0.6
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f'AI分析失敗：{e}'


# ═══════════════════════════════════════════════════════════
# 8. 生成 HTML
# ═══════════════════════════════════════════════════════════
def generate_html(market_rows, macro_data, news_items, giant_summaries, analysis_text, institutional):
    today     = datetime.now().strftime('%Y年%m月%d日')
    weekday   = ['週一','週二','週三','週四','週五','週六','週日'][datetime.now().weekday()]
    timestamp = datetime.now().strftime('%H:%M UTC')

    # 市場表格
    market_html = ''
    for r in market_rows:
        market_html += f'''<tr>
          <td>{r["name"]}</td>
          <td style="font-family:'IBM Plex Mono',monospace;">{r["price"]}</td>
          <td style="color:{r["color"]};font-weight:600;">{r["arrow"]}{r["pct"]}</td>
        </tr>'''

    # 總經
    macro_html = ''
    if macro_data:
        for m in macro_data:
            macro_html += f'<div class="macro-row"><span>{m["label"]}</span><span style="color:{m["color"]};font-family:\'IBM Plex Mono\',monospace;">{m["arrow"]} {m["val"]}</span></div>'
    else:
        macro_html = '<div class="macro-row muted">設定 FRED_API_KEY 後顯示（免費：fred.stlouisfed.org）</div>'

    # 新聞（翻譯後）
    news_html = ''
    for n in news_items:
        title = n['title_zh'] or n['title_en']
        en    = f'<span class="news-en">{n["title_en"]}</span>' if n['title_zh'] else ''
        link  = f'href="{n["link"]}"' if n['link'] else ''
        news_html += f'''<div class="news-item">
          <span class="news-source">{n["source"].replace("Google財經-","")}</span>
          <div><a {link} target="_blank" class="news-title">{title}</a>{en}</div>
        </div>'''

    # 巨人肩膀摘要
    giants_html = ''
    for g in giant_summaries:
        summary_text = g['summary'] or '今日無法取得內容'
        giants_html += f'''<div class="giant-card">
          <div class="giant-header">
            <div>
              <span class="giant-name">{g["name"]}</span>
              <span class="giant-focus">{g["focus"]}</span>
            </div>
            <a href="{g["url"]}" target="_blank" class="giant-link">前往原站 →</a>
          </div>
          <div class="giant-summary">{summary_text}</div>
        </div>'''

    # AI分析段落
    # 三大法人HTML
    inst_html = ''
    if institutional.get('data'):
        d = institutional['data']
        date_label = institutional.get('date', '前一交易日')
        # 固定順序：外資、投信、自營、合計
        order = [
            ('外資',    '外資及陸資'),
            ('投信',    '投信'),
            ('自營合計','自營商'),
            ('三大合計','三大法人合計'),
        ]
        for key, fallback_label in order:
            if key not in d:
                continue
            entry = d[key]
            label = entry.get('label', fallback_label)
            net   = entry['net']
            arrow, amt, color = fmt_yi(net)
            buy_yi  = entry['buy']  / 100_000_000
            sell_yi = entry['sell'] / 100_000_000
            is_total = key == '三大合計'
            weight = '700' if is_total else '400'
            border = 'border-top:1px solid #2a3347;margin-top:8px;padding-top:8px;' if is_total else ''
            inst_html += f'''<div class="macro-row" style="{border}">
              <span style="font-weight:{weight};">{label}</span>
              <span style="text-align:right;">
                <span style="color:{color};font-weight:{weight};font-family:'IBM Plex Mono',monospace;">{arrow} {amt}</span>
                <span class="muted" style="display:block;font-size:10px;">買{buy_yi:.2f}億 賣{sell_yi:.2f}億</span>
              </span>
            </div>'''
        inst_date_html = f'<div class="muted" style="font-size:10px;margin-bottom:10px;">資料日期：{date_label}</div>'
        inst_html = inst_date_html + inst_html
    else:
        inst_html = '<div class="macro-row muted">今日無資料（假日或API暫時無法連線）</div>'

    # AI分析段落
    analysis_html = ''.join(f'<p>{p.strip()}</p>' for p in analysis_text.split('\n') if p.strip())

    return f'''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
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

.header{{background:linear-gradient(135deg,#1a2744,#0f1117);border-bottom:1px solid var(--border);padding:28px 24px;text-align:center;}}
.header-date{{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--amber);letter-spacing:3px;margin-bottom:8px;}}
.header-title{{font-family:"Noto Serif TC",serif;font-size:clamp(20px,4vw,32px);font-weight:900;color:#fff;margin-bottom:6px;}}
.header-sub{{font-size:12px;color:var(--text2);font-family:"IBM Plex Mono",monospace;}}

.nav{{display:flex;justify-content:center;gap:8px;flex-wrap:wrap;padding:12px 24px;background:var(--bg2);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:50;}}
.nav a{{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--text2);padding:5px 12px;border:1px solid var(--border);border-radius:20px;transition:all .15s;white-space:nowrap;}}
.nav a:hover{{color:var(--amber);border-color:var(--amber);text-decoration:none;}}

.main{{max-width:960px;margin:0 auto;padding:28px 20px;display:grid;grid-template-columns:1fr 1fr;gap:20px;}}
.full{{grid-column:1/-1;}}
@media(max-width:640px){{.main{{grid-template-columns:1fr;}}.full{{grid-column:1;}}}}

.card{{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:20px;}}
.card-title{{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--amber);letter-spacing:2px;text-transform:uppercase;margin-bottom:14px;}}
.card-title::before{{content:"▸ ";}}

table{{width:100%;border-collapse:collapse;}}
th{{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--text2);padding:6px 8px;text-align:left;border-bottom:1px solid var(--border);}}
td{{padding:8px;border-bottom:1px solid rgba(42,51,71,.4);font-size:13px;font-family:"IBM Plex Mono",monospace;}}
tr:last-child td{{border-bottom:none;}}
tr:hover td{{background:rgba(245,158,11,.03);}}

.macro-row{{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(42,51,71,.4);font-size:13px;}}
.macro-row:last-child{{border-bottom:none;}}
.muted{{color:var(--text2);font-size:12px;}}

/* 新聞 */
.news-item{{padding:10px 0;border-bottom:1px solid rgba(42,51,71,.35);}}
.news-item:last-child{{border-bottom:none;}}
.news-source{{font-family:"IBM Plex Mono",monospace;font-size:9px;color:var(--text2);background:var(--bg3);padding:2px 7px;border-radius:10px;margin-right:6px;letter-spacing:1px;white-space:nowrap;}}
.news-title{{font-size:13px;color:var(--text);font-weight:500;}}
.news-title:hover{{color:var(--amber);}}
.news-en{{display:block;font-size:11px;color:var(--text2);margin-top:2px;font-family:"IBM Plex Mono",monospace;}}

/* 巨人肩膀 */
.giant-card{{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:16px;margin-bottom:12px;}}
.giant-card:last-child{{margin-bottom:0;}}
.giant-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;gap:12px;}}
.giant-name{{font-size:14px;font-weight:700;color:#fff;display:block;margin-bottom:3px;}}
.giant-focus{{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--blue);}}
.giant-link{{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--amber);white-space:nowrap;padding:4px 10px;border:1px solid rgba(245,158,11,.3);border-radius:4px;}}
.giant-link:hover{{background:rgba(245,158,11,.1);text-decoration:none;}}
.giant-summary{{font-size:13px;color:var(--text);line-height:1.8;background:rgba(0,0,0,.2);padding:12px;border-radius:4px;border-left:3px solid var(--amber);}}

/* AI分析 */
.ai-analysis p{{margin-bottom:14px;line-height:1.9;}}
.ai-analysis p:last-child{{margin-bottom:0;color:var(--text2);font-size:12px;}}

.footer{{text-align:center;padding:28px;font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--text2);border-top:1px solid var(--border);}}
.badge{{display:inline-block;background:rgba(34,197,94,.1);color:var(--green);border:1px solid rgba(34,197,94,.25);padding:4px 12px;border-radius:20px;font-size:10px;}}
</style>
</head>
<body>

<div class="header">
  <div class="header-date">▸ {today} {weekday} · 更新於 {timestamp}</div>
  <div class="header-title">📊 每日投資情報日報</div>
  <div class="header-sub">市場快照 · 總體經濟 · 產業新聞（中文）· 來源重點摘要 · AI分析</div>
</div>

<div class="nav">
  <a href="#market">市場快照</a>
  <a href="#macro">總體經濟</a>
  <a href="#inst">三大法人</a>
  <a href="#analysis">AI分析</a>
  <a href="#news">產業新聞</a>
  <a href="#giants">來源摘要</a>
</div>

<div class="main">

  <div class="card" id="market">
    <div class="card-title">市場快照</div>
    <table>
      <thead><tr><th>標的</th><th>價格</th><th>漲跌</th></tr></thead>
      <tbody>{market_html}</tbody>
    </table>
  </div>

  <div class="card" id="macro">
    <div class="card-title">總體經濟指標</div>
    {macro_html}
  </div>

  <div class="card" id="inst">
    <div class="card-title">🏦 台股三大法人買賣超</div>
    {inst_html}
  </div>

  <div class="card full" id="analysis">
    <div class="card-title">🤖 今日AI分析</div>
    <div class="ai-analysis">{analysis_html}</div>
  </div>

  <div class="card full" id="news">
    <div class="card-title">產業新聞（AI / 半導體 / 總體經濟）</div>
    {news_html}
  </div>

  <div class="card full" id="giants">
    <div class="card-title">今日來源重點摘要（AI自動整理）</div>
    {giants_html}
  </div>

</div>

<div class="footer">
  <div class="badge">● 每個交易日早上 09:00 自動更新</div><br><br>
  資訊整理僅供參考 · 不構成投資建議<br>
  數據來源：yfinance · FRED · Google News RSS · TrendForce · SemiAnalysis · Groq Llama 3.3
</div>

</body>
</html>'''


# ═══════════════════════════════════════════════════════════
# 8. 儲存 + Telegram通知
# ═══════════════════════════════════════════════════════════
def save_html(html_content):
    os.makedirs('docs', exist_ok=True)
    with open('docs/index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    print('✓ HTML已儲存 docs/index.html')


def send_telegram(market_rows, github_user, repo_name, institutional):
    today    = datetime.now().strftime('%Y/%m/%d')
    page_url = f'https://{github_user}.github.io/{repo_name}/'

    try:
        best  = max(market_rows, key=lambda x: x['raw_pct'])
        worst = min(market_rows, key=lambda x: x['raw_pct'])
        hi    = f"最強：{best['name']} <b>{best['pct']}</b>\n最弱：{worst['name']} <b>{worst['pct']}</b>"
    except:
        hi = ''

    # 三大法人摘要（外資最重要，排第一）
    inst_lines = ''
    if institutional.get('data'):
        d = institutional['data']
        date_label = institutional.get('date', '')
        inst_lines = f'\n\n🏦 <b>三大法人 ({date_label})</b>\n'
        order = [
            ('外資',    '外資'),
            ('投信',    '投信'),
            ('自營合計','自營'),
            ('三大合計','合計'),
        ]
        last_key = '三大合計'
        for key, label in order:
            if key not in d:
                continue
            _, amt, _ = fmt_yi(d[key]['net'])
            prefix = '└' if key == last_key else '├'
            bold_start = '<b>' if key in ('外資', '三大合計') else ''
            bold_end   = '</b>' if key in ('外資', '三大合計') else ''
            inst_lines += f'{prefix} {label}：{bold_start}{amt}{bold_end}\n'
        inst_lines = inst_lines.rstrip()

    msg = (
        f"📊 <b>投資日報 {today}</b>\n\n"
        f"{hi}"
        f"{inst_lines}\n\n"
        f"🔗 <a href='{page_url}'>點這裡看完整報告</a>\n"
        f"<i>含：市場快照・總經指標・三大法人・中文新聞・AI分析</i>"
    )
    r = requests.post(
        f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
        data={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
        timeout=30
    )
    print('✓ Telegram發送' if r.status_code == 200 else f'✗ 失敗：{r.text}')


# ═══════════════════════════════════════════════════════════
# 主程式
# ═══════════════════════════════════════════════════════════
def main():
    github_user = os.environ.get('GITHUB_USER', 'your-username')
    repo_name   = os.environ.get('REPO_NAME',   'daily-invest-bot')

    print('1. 市場數據...')
    market_rows = get_market_data()

    print('2. 總體經濟...')
    macro_data = get_fred_data()

    print('3. 台股三大法人...')
    institutional = get_twse_institutional()

    print('4. 新聞...')
    news_items = get_news()

    print('5. 翻譯新聞標題...')
    news_items = translate_news(news_items)

    print('6. 抓取來源摘要...')
    giant_summaries = get_giant_summaries()

    print('7. AI主分析...')
    analysis = generate_analysis(market_rows, macro_data, news_items, institutional)

    print('8. 生成HTML...')
    html = generate_html(market_rows, macro_data, news_items, giant_summaries, analysis, institutional)
    save_html(html)

    print('9. Telegram通知...')
    send_telegram(market_rows, github_user, repo_name, institutional)

    print('✓ 全部完成！')


if __name__ == '__main__':
    main()
