import os
import feedparser
import yfinance as yf
import requests
from datetime import datetime
from groq import Groq

# 從環境變數讀取（不要在這裡直接填密碼）
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
GROQ_API_KEY = os.environ['GROQ_API_KEY']
groq_client = Groq(api_key=GROQ_API_KEY)


def get_market_data():
    """取得主要市場指數和ETF數據"""
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
        
        '台灣加權': '^TWII',
        '台灣50(0050)': '0050.TW',
        '台積電(2330)': '2330.TW',
    }
    lines = []
    for name, sym in symbols.items():
        try:
            t = yf.Ticker(sym)
            hist = t.history(period='5d')
            if len(hist) >= 2:
                price = hist['Close'].iloc[-1]
                prev = hist['Close'].iloc[-2]
                pct = (price - prev) / prev * 100
                arrow = '▲' if pct > 0 else '▼'
                lines.append(f"{name}: {price:.2f} {arrow}{abs(pct):.2f}%")
        except Exception as e:
            print(f"跳過 {sym}: {e}")
    return '\n'.join(lines) if lines else "今日市場數據暫時無法取得"


def get_news():
    """取得最新財經新聞標題"""
    feeds = [
        ('Yahoo財經', 'https://finance.yahoo.com/news/rssindex'),
        ('MarketWatch', 'https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines'),
        ('Seeking Alpha', 'https://seekingalpha.com/feed.xml'),
    ]
    news_items = []
    for source, url in feeds:
        try:
            feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
            for entry in feed.entries[:3]:
                title = entry.get('title', '')
                if title:
                    news_items.append(f"• [{source}] {title}")
            if news_items:
                break  # 有拿到就停，不用全部試
        except Exception as e:
            print(f"{source} 失敗：{e}")
            continue
    return '\n'.join(news_items[:5]) if news_items else "新聞暫時無法取得"


def generate_analysis(market_data, news):
    """用Groq生成今日分析"""
    today = datetime.now().strftime('%Y/%m/%d')
    
    prompt = f"""今天是 {today}。

以下是今日市場數據：
{market_data}

請用繁體中文，針對以下三點各寫1-2句話，語氣像朋友聊天，不要廢話：

1. 【今天漲跌的主因】根據數據，今天整體是偏多還是偏空？最強和最弱的是哪個？
2. 【長期ETF投資者要注意什麼】對持有VT、QQQ、0050這類ETF的人，今天的數據有沒有需要留意的訊號？還是繼續持有就好？
3. 【一句話總結】今天市場給你的感覺是什麼？

最後固定加一行：「以上是資訊分享，不是買賣建議 😊」

不要加標題，不要條列，直接寫成對話口吻的段落。"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "你是一個懂投資的朋友，說話直接、有重點，不說廢話，不說『我無法預測市場』這類沒用的話。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=400,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI分析暫時無法生成：{e}"


def send_to_telegram(message):
    """發送訊息到Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML'
    }
    r = requests.post(url, data=data, timeout=30)
    if r.status_code == 200:
        print("✓ 成功發送到Telegram")
    else:
        print(f"✗ 發送失敗：{r.text}")


def main():
    today = datetime.now().strftime('%Y/%m/%d %A')
    print(f"開始執行：{today}")

    print("正在取得市場數據...")
    market_data = get_market_data()

    # print("正在取得新聞...")
    # news = get_news()

    # print("正在生成AI分析...")
    # analysis = generate_analysis(market_data, news)

    message = (
        f"📊 每日投資簡報 {datetime.now().strftime('%Y/%m/%d')}\n\n"
        f"市場快照\n"
        f"{market_data}"
        # f"今日新聞\n"
        # f"{news}\n\n"
        # f"🤖 今日分析\n"
        # f"{analysis}"
    )

    print("發送到Telegram...")
    send_to_telegram(message)
    print("完成！")


if __name__ == '__main__':
    main()
