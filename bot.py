import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from google import genai

def main():
    # 1. RSSから記事を取得
    rss_url = "https://rssblog.ameba.jp/angerme-new/rss20.xml"
    response = urllib.request.urlopen(rss_url)
    xml_data = response.read()
    root = ET.fromstring(xml_data)
    
    # 2. 現在時刻と24時間前の基準時刻を設定 (JST基準)
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    one_day_ago = now - timedelta(days=1)
    
    items = root.findall(".//item")
    print(f"現在の確認時刻: {now.strftime('%Y-%m-%d %H:%M:%S')} (JST)")
    print(f"過去24時間（{one_day_ago.strftime('%Y-%m-%d %H:%M:%S')} 以降）の新規投稿をチェックします。\n")

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    match_count = 0

    for item in items:
        title = item.find("title").text
        description = item.find("description").text
        
        # テーマ（category）の取得
        category_tag = item.find("category")
        theme = category_tag.text if category_tag is not None else "テーマなし"
        
        # 投稿日時のパース (例: "Mon, 06 Jul 2026 21:00:00 +0900")
        pub_date_str = item.find("pubDate").text
        # RSSの標準的な時間フォーマットをパース
        try:
            pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %z")
        except ValueError:
            continue

        # 3. 24時間以内の投稿かどうかを判定
        if pub_date >= one_day_ago:
            match_count += 1
            print(f"--- 【新規投稿検知】テーマ: {theme} / タイトル: {title} ---")
            
            # プロンプトの組み立て（場所特定禁止のルールをここで先んじて仕込みます）
            prompt = (
                f"以下のブログ記事はアンジュルムのメンバーによる投稿です。\n"
                f"テーマ（メンバー名）: {theme}\n"
                f"タイトル: {title}\n"
                f"本文: {description}\n\n"
                f"【要約のルール】\n"
                f"1. タイムラインに表示する「超要約（短文）」と、詳細がわかる「本文（長文）」に分けて出力してください。\n"
                f"2. ブログ内の具体的な場所（聖地や撮影場所など）を特定・推測できるような情報の記載は絶対に禁止してください。"
            )
            
            # Geminiで要約生成
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            summary = response.text

            print("[生成された要約内容]")
            print(summary)
            print("-" * 50 + "\n")

    if match_count == 0:
        print("過去24時間以内に新しいブログ投稿はありませんでした。")

if __name__ == "__main__":
    main()
