"""
line_market.py
每日歐美股市指數通知 → LINE
使用 LINE Notify（免費，不需要申請Bot）
"""
import os
import requests
from datetime import datetime, timedelta
import pytz

# ══════════════════════════════════════════════
# 1. 抓取指數數據（yfinance）
# ══════════════════════════════════════════════

INDICES = [
    # 美股
    {'symbol': '^DJI',   'name': '道瓊工業',   'flag': '🇺🇸'},
    {'symbol': '^GSPC',  'name': 'S&P500',    'flag': '🇺🇸'},
    {'symbol': '^NDX',  'name': '那斯達克100',      'flag': '🇺🇸'},
    {'symbol': '^SOX',   'name': '費城半導體',   'flag': '🇺🇸'},
    {'symbol': 'TSM',   'name': '台積電ADR',   'flag': '🇺🇸'},
    # 歐股
    {'symbol': '^GDAXI', 'name': '德國股市',         'flag': '🇩🇪'},
    {'symbol': '^FTSE',  'name': '英國股市',    'flag': '🇬🇧'},
    {'symbol': '^FCHI',  'name': '法國股市',      'flag': '🇫🇷'},
]

def fetch_indices():
    import yfinance as yf
    from datetime import datetime, timedelta, timezone

    results = []
    for idx in INDICES:
        try:
            ticker = yf.Ticker(idx['symbol'])
            
            # ✅ 改用 5d，保留緩衝，避免邊界問題
            hist = ticker.history(period='5d')
            
            if len(hist) < 2:
                continue

            # ✅ 過濾掉「今天還沒收盤的資料」
            # yfinance 有時會回傳當天不完整的 bar
            now_utc = datetime.now(timezone.utc)
            hist.index = hist.index.tz_convert('UTC')  # 統一轉成 UTC 比較
            
            # 只保留「收盤時間已超過 30 分鐘前」的資料（避免抓到當天未完整的 bar）
            hist = hist[hist.index < (now_utc - timedelta(minutes=30))]

            if len(hist) < 2:
                continue

            prev  = hist['Close'].iloc[-2]
            close = hist['Close'].iloc[-1]
            change = close - prev
            pct    = (change / prev) * 100

            # ✅ 順便印出日期，方便驗證
            latest_date = hist.index[-1].strftime('%Y-%m-%d')
            print(f"  ✓ {idx['name']}: {close:,.2f} ({change:+.2f} / {pct:+.2f}%) [{latest_date}]")

            results.append({
                **idx,
                'price':  close,
                'change': change,
                'pct':    pct,
                'status': 'ok',
                'date':   latest_date  # 建議也存起來方便 debug
            })

        except Exception as e:
            print(f"  ✗ {idx['name']}: {e}")
    return results


# ══════════════════════════════════════════════
# 2. 組裝 LINE 訊息
# ══════════════════════════════════════════════

def format_message(results):
    tw = pytz.timezone('Asia/Taipei')
    now = datetime.now(tw)
    today = now.strftime('%m/%d %H:%M')

    lines = [f"\n📊 每日市場指數 {today}"]
    lines.append("─────────────────")

    # 分區段
    us_lines  = []
    eu_lines  = []

    for r in results:
        if r['status'] not in ('ok', 'no_prev'):
            continue

        price  = r['price']
        change = r.get('change', 0)
        pct    = r.get('pct', 0)

        # 漲跌符號
        if pct > 0:
            arrow = '▲'
        elif pct < 0:
            arrow = '▼'
        else:
            arrow = '─'

        # 格式化數字
        if price >= 10000:
            price_str = f"{price:,.0f}"
        else:
            price_str = f"{price:,.2f}"

        if r['status'] == 'no_prev':
            line = f"{r['flag']} {r['name']}\n   {price_str}  （無前日數據）"
        else:
            line = (
                f"{r['flag']} {r['name']}\n"
                f"   {price_str}  {arrow}{abs(change):,.2f} ({pct:+.2f}%)"
            )

        sym = r['symbol']
        if sym in ['^GSPC', '^NDX', '^DJI','^SOX','TSM']:
            us_lines.append(line)
        else:
            eu_lines.append(line)

    if us_lines:
        lines.append("🇺🇸 美股")
        lines.extend(us_lines)

    if eu_lines:
        lines.append("─────────────────")
        lines.append("🌍 歐股")
        lines.extend(eu_lines)


    # 整體市場氣氛
    ok_results = [r for r in results if r['status'] == 'ok']
    if ok_results:
        up   = sum(1 for r in ok_results if r['pct'] > 0)
        down = sum(1 for r in ok_results if r['pct'] < 0)
        flat = len(ok_results) - up - down
        lines.append("─────────────────")
        lines.append(f"📈 上漲 {up}  📉 下跌 {down}  ➡ 持平 {flat}")

    return '\n'.join(lines)


# ══════════════════════════════════════════════
# 3. 發送到 LINE Notify
# ══════════════════════════════════════════════

def send_line(message):
    """LINE Messaging API Push Message"""
    r = requests.post(
        'https://api.line.me/v2/bot/message/push',
        headers={
            'Authorization': f'Bearer {os.environ["LINE_CHANNEL_TOKEN"]}',
            'Content-Type': 'application/json'
        },
        json={
            'to': os.environ['LINE_GROUP_ID'],
            'messages': [{'type': 'text', 'text': message}]
        },
        timeout=15
    )
    if r.status_code == 200:
        print('✓ LINE 發送成功')
    else:
        print(f'✗ LINE 失敗: {r.status_code} {r.text}')


# ══════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════

def main():
    tw = pytz.timezone('Asia/Taipei')
    now = datetime.now(tw)
    print(f"\n=== LINE 市場指數通知 {now.strftime('%Y-%m-%d %H:%M')} ===\n")

    print("【Step 1】抓取指數...")
    results = fetch_indices()

    if not results:
        send_line('\n⚠️ 今日無法取得市場數據，可能為假日或資料延遲。')
        return

    print("\n【Step 2】組裝訊息...")
    message = format_message(results)
    print(message)

    print("\n【Step 3】發送 LINE...")
    send_line(message)

    print("\n=== 完成 ===")


if __name__ == '__main__':
    main()
