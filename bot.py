import os
import urllib.request
import xml.etree.ElementTree as ET
from google import genai

def main():
    # RSSから記事を取得
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

    # 【一時改修】Xへの投稿はせず、ログに出力してチェックできるようにする
    print("--- [チェック用] 生成された要約内容ここから ---")
    print(summary)
    print("--- [チェック用] 生成された要約内容ここまで ---")
    print("※現在はテストモードのため、Xへの投稿はスキップしました。")

if __name__ == "__main__":
    main()
