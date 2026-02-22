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
        ('路透科技', 'https://feeds.reuters.com/reuters/technologyNews'),
        ('路透財經', 'https://feeds.reuters.com/reuters/businessNews'),
    ]
    news_items = []
    for source, url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                news_items.append(f"• {entry.title}")
        except:
            pass
    return '\n'.join(news_items[:6]) if news_items else "新聞暫時無法取得"


def generate_analysis(market_data, news):
    """用Gemini生成今日分析"""
    today = datetime.now().strftime('%Y/%m/%d')
    prompt = f"""你是一個每天發投資簡報給朋友的人，用繁體中文，語氣輕鬆像朋友聊天。

今天是 {today}。

【市場數據】
{market_data}

【今日新聞標題】
{news}

請寫一段200字以內的分析，包含：
1. 今天市場整體感覺（一句話）
2. 有沒有特別值得注意的事（如果有的話）
3. 對長期持有VT、QQQ、台灣ETF的人，今天有什麼值得知道的

最後一行固定加上：「以上是資訊分享，不是買賣建議 😊」

不要用條列式，直接寫成自然的段落。"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500
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

    print("正在取得新聞...")
    news = get_news()

    print("正在生成AI分析...")
    analysis = generate_analysis(market_data, news)

    message = (
        f"📊 每日投資簡報 {datetime.now().strftime('%Y/%m/%d')}\n\n"
        f"市場快照\n"
        f"{market_data}\n\n"
        f"🤖 今日分析\n"
        f"{analysis}"
    )

    print("發送到Telegram...")
    send_to_telegram(message)
    print("完成！")


if __name__ == '__main__':
    main()
