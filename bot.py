import os
import urllib.request
import xml.etree.ElementTree as ET
from google import genai
import tweepy # X投稿用ライブラリ

def main():
    # RSSから記事を取得（以前のコードと同じ）
    rss_url = "https://rssblog.ameba.jp/angerme-ss-shin/rss20.xml"
    response = urllib.request.urlopen(rss_url)
    xml_data = response.read()
    root = ET.fromstring(xml_data)
    latest_item = root.find(".//item")
    title = latest_item.find("title").text
    description = latest_item.find("description").text

    # Geminiで要約
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    prompt = f"要約してください：{title} \n {description}"
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    summary = response.text

    # X(Twitter)に投稿
    auth = tweepy.OAuth1UserHandler(
        os.environ.get("TWITTER_API_KEY"),
        os.environ.get("TWITTER_API_SECRET"),
        os.environ.get("TWITTER_ACCESS_TOKEN"),
        os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
    )
    api = tweepy.API(auth)
    api.update_status(status=summary)
    print("投稿完了！")

if __name__ == "__main__":
    main()
