import os
import urllib.request
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from google import genai
from google.genai import types

def main():
    # 1. RSSからすべての記事を取得
    rss_url = "https://rssblog.ameba.jp/angerme-new/rss20.xml"
    response = urllib.request.urlopen(rss_url)
    xml_data = response.read()
    root = ET.fromstring(xml_data)
    items = root.findall(".//item")
    
    # 2. 基準時刻の設定 (JST基準)
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    one_day_ago = now - timedelta(days=1)
    
    print(f"現在時刻: {now.strftime('%Y-%m-%d %H:%M:%S')} (JST)")
    print("過去24時間の新着記事を対象に、過去ログの文脈と画像を分析します。\n")

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    # 全記事をパースしてリスト化
    all_posts = []
    for item in items:
        title = item.find("title").text
        description = item.find("description").text
        category_tag = item.find("category")
        theme = category_tag.text if category_tag is not None else "テーマなし"
        pub_date_str = item.find("pubDate").text
        try:
            pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %z")
        except ValueError:
            continue
        
        all_posts.append({
            "theme": theme,
            "title": title,
            "description": description,
            "pub_date": pub_date
        })

    match_count = 0

    # 3. 各記事をループ（新着記事を特定）
    for post in all_posts:
        if post["pub_date"] >= one_day_ago:
            match_count += 1
            current_theme = post["theme"]
            
            print(f"--- 【分析対象】テーマ: {current_theme} / タイトル: {post['title']} ---")
            
            # 4. 同じメンバー（テーマ）の直近の過去記事をコンテキストとして抽出
            past_context = ""
            context_count = 1
            for past in all_posts:
                # 自分自身より古く、同じテーマの記事を3件ほど文脈として拾う
                if past["theme"] == current_theme and past["pub_date"] < post["pub_date"]:
                    past_context += f"【過去記事{context_count}】タイトル: {past['title']}\n本文一部: {past['description'][:300]}...\n\n"
                    context_count += 1
                    if context_count > 3: # 直近3件分で文脈としては十分です
                        break

            # 5. 本文から画像URL（<img>タグ）を抽出
            img_urls = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', post["description"])
            
            # Geminiに渡す中身（あとに画像を追加できるようにリスト型にする）
            contents = []
            
            # プロンプトの組み立て
            prompt_text = (
                f"あなたはアンジュルムの熱心なファンであり、優秀な広報アシスタントです。\n"
                f"以下の情報をもとに、今回のブログの要約を作成してください。\n\n"
                f"■ メンバー名(テーマ): {current_theme}\n"
                f"■ 今回のブログタイトル: {post['title']}\n"
                f"■ 今回の本文: {post['description']}\n\n"
                f"■ 直近の過去記事の文脈:\n{past_context if past_context else '直近に過去投稿なし'}\n"
                f"【要約のルール】\n"
                f"1. 出力は必ず「メンバー」毎に「要約（短文、3行まで）」にしてください。\n"
                f"2. 直近の動き（過去記事の文脈）と今回のブログ内容に繋がりがあれば、それを織り交ぜてストーリー性を持たせてください。\n"
                f"3. 添付された画像がある場合、その写真に写っているメンバーの表情や様子、衣装なども分析して要約に反映させてください。\n"
                f"4. 【厳禁】ブログ内の具体的な場所（聖地や撮影場所、ロケ地など）を特定・推測できるような情報の記載は、安全のため絶対に禁止します。\n"
            )
            contents.append(prompt_text)

            # 画像URLがあれば、先頭の1枚をGeminiに読み込ませる
            if img_urls:
                target_img = img_urls[0]
                # アメブロの画像URLのプロトコルを補正
                if target_img.startswith("//"):
                    target_img = "https:" + target_img
                print(f"[画像検知] 分析対象URL: {target_img}")
                try:
                    img_data = urllib.request.urlopen(target_img).read()
                    contents.append(types.Part.from_bytes(data=img_data, mime_type="image/jpeg"))
                except Exception as e:
                    print(f"[画像読み込み失敗] {e}（テキストのみで解析します）")

            # 6. Geminiで生成
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=contents
                )
                print("\n[Geminiが生成した要約結果]")
                print(response.text)
            except Exception as e:
                print(f"Gemini APIエラー: {e}")
                
            print("-" * 60 + "\n")

    if match_count == 0:
        print("過去24時間以内に新しいブログ投稿はありませんでした。（手動テスト用に基準を緩める場合はコードの timedelta を調整してください）")

if __name__ == "__main__":
    main()
