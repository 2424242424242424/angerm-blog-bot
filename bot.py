import os
import urllib.request
import xml.etree.ElementTree as ET
from google import genai

def main():
    # 1. ブログのRSSから最新の1件を取得する
    rss_url = "https://rssblog.ameba.jp/angerme-ss-shin/rss20.xml"
    
    try:
        response = urllib.request.urlopen(rss_url)
        xml_data = response.read()
        root = ET.fromstring(xml_data)
        
        latest_item = root.find(".//item")
        title = latest_item.find("title").text
        description = latest_item.find("description").text
        
        # 2. GitHubのシークレットから安全にAPIキーを読み込む設定
        api_key = os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)
        
        prompt = f"""
以下のアンジュルムのブログ記事を読み、X（Twitter）に投稿するための要約を日本語で作成してください。

【制約事項】
・文字数は100文字程度
・ハッシュタグ「#アンジュルム」をつける
・親しみやすいトーンで

【記事タイトル】
{title}

【本文】
{description}
"""
        
        print("Geminiが要約を作成中...")
        api_response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        
        print("\n--- 生成されたX投稿用要約 ---")
        print(api_response.text)
        
    except Exception as e:
        print(f"エラーが発生しました: {e}")

if __name__ == "__main__":
    main()
