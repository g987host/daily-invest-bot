"""
monthly_check.py
每月投資環境自動檢查
執行一次，抓取所有指標，AI分析，發送到Telegram
"""
import os
import requests
from datetime import datetime
from groq import Groq

TELEGRAM_TOKEN   = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
GROQ_API_KEY     = os.environ['GROQ_API_KEY']
FRED_API_KEY     = os.environ.get('FRED_API_KEY', '')

groq_client = Groq(api_key=GROQ_API_KEY)


# ══════════════════════════════════════════════
# 1. 抓取所有指標
# ══════════════════════════════════════════════

def fred_get(series_id, limit=2):
    """從FRED取得指標數據"""
    if not FRED_API_KEY:
        return None
    try:
        r = requests.get(
            'https://api.stlouisfed.org/fred/series/observations',
            params={
                'series_id': series_id,
                'api_key': FRED_API_KEY,
                'file_type': 'json',
                'limit': limit,
                'sort_order': 'desc'
            },
            timeout=10
        )
        obs = r.json().get('observations', [])
        return [o['value'] for o in obs if o['value'] != '.']
    except Exception as e:
        print(f"FRED {series_id} 失敗: {e}")
        return None


def get_cape():
    """優先手動輸入，否則嘗試自動抓"""
    import re
    manual = os.environ.get('CAPE_MANUAL', '').strip()
    if manual:
        try:
            val = float(manual)
            print(f"  ✓ CAPE (手動): {val}")
            return val
        except:
            pass
    try:
        r = requests.get(
            'https://www.multpl.com/shiller-pe',
            timeout=15,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        m = re.search(r'id="current-value"[^>]*>\s*([\d.]+)', r.text)
        if m:
            val = float(m.group(1))
            if 5 < val < 100:
                print(f"  ✓ CAPE (自動): {val}")
                return val
    except Exception as e:
        print(f"  CAPE 自動失敗: {e}")
    print("  ⚠ CAPE: 請手動輸入")
    return None


def fetch_all_indicators():
    """抓取所有五個指標"""
    results = {}

    print("正在抓取指標...")

    # ── 1. Fed 利率 ──────────────────────────────
    fed = fred_get('FEDFUNDS', 3)
    if fed and len(fed) >= 2:
        current = float(fed[0])
        prev    = float(fed[1])
        direction = '持平'
        if current > prev: direction = '升息中'
        elif current < prev: direction = '降息中'
        results['fed'] = {
            'value': current,
            'prev': prev,
            'direction': direction,
            'raw': fed
        }
        print(f"  ✓ Fed利率: {current}% ({direction})")
    else:
        results['fed'] = None
        print("  ✗ Fed利率: 無法取得（需要FRED_API_KEY）")

    # ── 2. 殖利率曲線 ────────────────────────────
    dgs10 = fred_get('DGS10', 3)
    dgs2  = fred_get('DGS2', 3)
    if dgs10 and dgs2:
        v10 = float(dgs10[0])
        v2  = float(dgs2[0])
        spread = v10 - v2
        # 判斷倒掛後回正（最危險）
        prev_spread = float(dgs10[1]) - float(dgs2[1]) if len(dgs10) > 1 and len(dgs2) > 1 else spread
        was_inverted = prev_spread < 0
        is_now_positive = spread > 0
        reverting = was_inverted and is_now_positive  # 倒掛後回正
        results['yield_curve'] = {
            'spread': spread,
            'dgs10': v10,
            'dgs2': v2,
            'inverted': spread < 0,
            'reverting': reverting,
        }
        status = '倒掛後回正（⚠️ 最危險）' if reverting else ('倒掛中' if spread < 0 else '正常')
        print(f"  ✓ 殖利率曲線: {spread:.2f}% ({status})")
    else:
        results['yield_curve'] = None
        print("  ✗ 殖利率曲線: 無法取得")

    # ── 3. 薩姆法則 ──────────────────────────────
    sahm = fred_get('SAHMREALTIME', 2)
    if sahm:
        val = float(sahm[0])
        if val >= 0.5:
            status = '衰退確認'
        elif val >= 0.3:
            status = '警戒區'
        else:
            status = '安全'
        results['sahm'] = {'value': val, 'status': status}
        print(f"  ✓ 薩姆法則: {val} ({status})")
    else:
        results['sahm'] = None
        print("  ✗ 薩姆法則: 無法取得")

    # ── 4. ISM PMI ───────────────────────────────
    # 優先手動輸入（FRED有延遲，手動最準確）
    pmi_manual = os.environ.get('PMI_MANUAL', '').strip()
    pmi_val = None

    if pmi_manual:
        try:
            pmi_val = float(pmi_manual)
            print(f"  ✓ ISM PMI (手動): {pmi_val}")
        except:
            pass

    if pmi_val is None:
        # 嘗試FRED（可能有1個月延遲）
        pmi_data = fred_get('NAPM', 3)
        if pmi_data:
            try:
                pmi_val = float(pmi_data[0])
                print(f"  ✓ ISM PMI (FRED，可能有延遲): {pmi_val}")
            except:
                pass

    if pmi_val is not None:
        # 前期：優先手動輸入
        prev_manual = os.environ.get('PMI_PREV', '').strip()
        prev_val = None
        if prev_manual:
            try:
                prev_val = float(prev_manual)
            except:
                pass
        # 手動沒有就從FRED抓第二筆
        if prev_val is None:
            fred_data = fred_get('NAPM', 3)
            if fred_data and len(fred_data) >= 2:
                try:
                    prev_val = float(fred_data[1])
                except:
                    pass
        if prev_val is None:
            prev_val = pmi_val  # 真的沒有就顯示持平

        trend  = '上升' if pmi_val > prev_val else ('下降' if pmi_val < prev_val else '持平')
        status = '擴張' if pmi_val > 50 else '收縮'
        results['pmi'] = {
            'value': pmi_val,
            'prev': prev_val,
            'trend': trend,
            'status': status
        }
        print(f"  ✓ ISM PMI: {pmi_val} ({status}，{trend})")
    else:
        results['pmi'] = None
        print("  ✗ ISM PMI: 請手動輸入")

    # ── 5. Shiller CAPE ──────────────────────────
    cape = get_cape()
    if not cape:
        # 備用：使用最近已知值（2026年2月約37倍，每季請手動確認一次）
        cape = 37.0
        print("  ⚠ CAPE: 使用備用數值 37.0（網路抓取失敗）")
    if cape > 30:
        valuation = '偏貴（謹慎加碼）'
    elif cape > 20:
        valuation = '合理區間'
    else:
        valuation = '便宜（好時機）'
    results['cape'] = {'value': cape, 'valuation': valuation}
    print(f"  ✓ Shiller CAPE: {cape} ({valuation})")

    return results


# ══════════════════════════════════════════════
# 2. AI分析（科斯托蘭尼 + 建議）
# ══════════════════════════════════════════════

def ai_analyze(indicators):
    """用AI分析指標，給出科斯托蘭尼位置和建議"""

    # 組裝指標文字
    lines = []

    if indicators.get('fed'):
        f = indicators['fed']
        lines.append(f"Fed利率：{f['value']}%，方向：{f['direction']}")
    else:
        lines.append("Fed利率：資料缺失，請自行查看CME FedWatch")

    if indicators.get('yield_curve'):
        yc = indicators['yield_curve']
        status = '倒掛後回正（最危險）' if yc['reverting'] else ('倒掛中' if yc['inverted'] else '正常正斜率')
        lines.append(f"殖利率曲線（10Y-2Y）：{yc['spread']:.2f}%，狀態：{status}")
    else:
        lines.append("殖利率曲線：資料缺失")

    if indicators.get('sahm'):
        s = indicators['sahm']
        lines.append(f"薩姆法則：{s['value']}（{s['status']}）")
    else:
        lines.append("薩姆法則：資料缺失")

    if indicators.get('pmi'):
        p = indicators['pmi']
        lines.append(f"ISM製造業PMI：{p['value']}（{p['status']}，趨勢{p['trend']}）")
    else:
        lines.append("ISM PMI：資料缺失")

    if indicators.get('cape'):
        c = indicators['cape']
        lines.append(f"Shiller CAPE本益比：{c['value']}（{c['valuation']}）")
    else:
        lines.append("Shiller CAPE：資料缺失")

    indicators_text = '\n'.join(lines)
    today = datetime.now().strftime('%Y年%m月')

    prompt = f"""你是一位資深投資顧問，請根據以下 {today} 的市場指標，給出完整的月度分析。

【五大指標】
{indicators_text}

請依序分析以下四個部分：

**第一部分：燈號判定**
根據指標，現在是綠燈（安心持有）、黃燈（觀望）還是紅燈（警覺）？用一句話說明理由。

**第二部分：科斯托蘭尼雞蛋定位**
現在在循環的哪個位置？
- 位置1：底部（利率高峰，資金最緊，悲觀情緒最重）
- 位置2：上升段（利率開始降，股市緩步回升）
- 位置3：頂部（資金氾濫，全民瘋股票）
- 位置4：下降段（利率上升，股市下跌）
說明為什麼是這個位置，以及這個位置代表什麼意義。

**第三部分：具體行動建議**
針對持有 VT（全球ETF）、QQQ（科技ETF）、SOXX（半導體ETF）、0050（台灣50）的長期投資者：
- 這個月的定期定額：繼續 / 暫停 / 加碼？
- 持倉比例需要調整嗎？
- 有什麼特別需要注意的事？

**第四部分：下個月要關注什麼**
找尋1周內最新相關新聞後，列出3-4個下個月最值得追蹤的指標或事件。

語氣直接，說人話，不超過400字，不要廢話。"""

    try:
        resp = groq_client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[
                {
                    'role': 'system',
                    'content': '你是有十五年經驗的投資研究員，熟悉科斯托蘭尼理論和總體經濟分析，說話簡潔有重點，只說有數據支撐的事。'
                },
                {'role': 'user', 'content': prompt}
            ],
            max_tokens=800,
            temperature=0.5
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"AI分析失敗：{e}"


# ══════════════════════════════════════════════
# 3. 發送到Telegram
# ══════════════════════════════════════════════

def send_telegram(indicators, analysis):
    today = datetime.now().strftime('%Y年%m月')

    # 組裝指標摘要
    def fmt(key, label, fmt_fn):
        if indicators.get(key):
            return fmt_fn(indicators[key])
        return f"{label}：資料缺失"

    # 燈號
    lights = []
    if indicators.get('fed'):
        d = indicators['fed']['direction']
        lights.append('🟢' if '降' in d else ('🔴' if '升' in d else '🟡'))
    if indicators.get('yield_curve'):
        yc = indicators['yield_curve']
        lights.append('🔴' if yc['reverting'] else ('🟡' if yc['inverted'] else '🟢'))
    if indicators.get('sahm'):
        v = indicators['sahm']['value']
        lights.append('🔴' if v >= 0.5 else ('🟡' if v >= 0.3 else '🟢'))
    if indicators.get('pmi'):
        v = indicators['pmi']['value']
        lights.append('🟢' if v > 52 else ('🔴' if v < 48 else '🟡'))
    if indicators.get('cape'):
        v = indicators['cape']['value']
        lights.append('🔴' if v > 33 else ('🟡' if v > 22 else '🟢'))

    red_count    = lights.count('🔴')
    green_count  = lights.count('🟢')
    overall = '🟢 綠燈' if red_count == 0 and green_count >= 3 else ('🔴 紅燈' if red_count >= 2 else '🟡 黃燈')

    # 指標文字
    ind_lines = []
    if indicators.get('fed'):
        f = indicators['fed']
        ind_lines.append(f"📌 Fed利率 {f['value']}% · {f['direction']}")
    if indicators.get('yield_curve'):
        yc = indicators['yield_curve']
        status = '⚠️倒掛後回正' if yc['reverting'] else ('倒掛中' if yc['inverted'] else '正常')
        ind_lines.append(f"📌 殖利率曲線 {yc['spread']:.2f}% · {status}")
    if indicators.get('sahm'):
        s = indicators['sahm']
        ind_lines.append(f"📌 薩姆法則 {s['value']} · {s['status']}")
    if indicators.get('pmi'):
        p = indicators['pmi']
        ind_lines.append(f"📌 ISM PMI {p['value']} · {p['status']}{p['trend']}")
    if indicators.get('cape'):
        c = indicators['cape']
        ind_lines.append(f"📌 Shiller CAPE {c['value']} · {c['valuation']}")

    indicators_str = '\n'.join(ind_lines) if ind_lines else '（需設定FRED_API_KEY）'
    lights_str = ' '.join(lights)

    msg = (
        f"📊 <b>月度投資環境檢查 · {today}</b>\n\n"
        f"<b>五大指標</b>\n"
        f"{indicators_str}\n\n"
        f"<b>燈號</b>  {lights_str}\n"
        f"<b>整體判定：{overall}</b>\n\n"
        f"─────────────────\n\n"
        f"<b>🤖 AI分析與行動建議</b>\n\n"
        f"{analysis}\n\n"
        f"─────────────────\n"
        f"<i>以上是資訊整理，不是投資建議。</i>\n\n"
        f"─────────────────\n"
        f"<b>📎 查詢來源連結</b>\n"
        f"• <a href='https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html'>Fed利率預期 · CME FedWatch</a>\n"
        f"• <a href='https://fred.stlouisfed.org/graph/?g=A9Ed'>殖利率曲線 · FRED（10Y-2Y）</a>\n"
        f"• <a href='https://fred.stlouisfed.org/series/SAHMREALTIME'>薩姆法則 · FRED</a>\n"
        f"• <a href='https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/pmi/'>ISM PMI · 官方網站</a>\n"
        f"• <a href='https://www.multpl.com/shiller-pe'>Shiller CAPE · multpl.com</a>"
    )

    # Telegram限制4096字，超過就分兩則
    if len(msg) > 4000:
        part1 = (
            f"📊 <b>月度投資環境檢查 · {today}</b>\n\n"
            f"<b>五大指標</b>\n{indicators_str}\n\n"
            f"<b>燈號</b>  {lights_str}\n"
            f"<b>整體判定：{overall}</b>"
        )
        part2 = (
            f"<b>🤖 AI分析與行動建議</b>\n\n"
            f"{analysis}\n\n"
            f"<i>以上是資訊整理，不是投資建議。</i>"
        )
        for part in [part1, part2]:
            _send(part)
    else:
        _send(msg)


def _send(text):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    r = requests.post(url, data={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML'
    }, timeout=30)
    if r.status_code == 200:
        print('✓ Telegram發送成功')
    else:
        print(f'✗ Telegram失敗: {r.text}')


# ══════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════

def main():
    print(f"\n=== 月度投資環境檢查 {datetime.now().strftime('%Y-%m-%d')} ===\n")

    print("【Step 1】抓取五大指標...")
    indicators = fetch_all_indicators()

    print("\n【Step 2】AI分析中...")
    analysis = ai_analyze(indicators)
    print(f"  ✓ 分析完成")
    print(f"\n{analysis}\n")

    print("【Step 3】發送到Telegram...")
    send_telegram(indicators, analysis)

    print("\n=== 完成 ===")


if __name__ == '__main__':
    main()
