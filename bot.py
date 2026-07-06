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
    # デバッグ用に新着の判定基準を広げたい場合は、ここの days=1 を days=3 などに一時的に広げてください
    one_day_ago = now - timedelta(days=1)
    
    print(f"現在時刻: {now.strftime('%Y-%m-%d %H:%M:%S')} (JST)")
    print("【デバッグモード】要約結果をログに出力します（Xへの投稿は行いません）。\n")

    client_gemini = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    # 全記事をパースしてリスト化
    all_posts = []
    for item in items:
        title = item.find("title").text
        description = item.find("description").text
        category_tag = item.find("category")
        
        # テーマが取得できない、または空の場合は None にする
        theme = category_tag.text if category_tag is not None else None
        
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

    tweet_lines = []

    # 3. 各記事をループ（新着記事を特定）
    for post in all_posts:
        if post["pub_date"] >= one_day_ago:
            current_theme = post["theme"]
            
            # 4. 同じメンバー（テーマ）の直近の过去記事をコンテキストとして抽出
            past_context = ""
            context_count = 1
            for past in all_posts:
                if current_theme and past["theme"] == current_theme and past["pub_date"] < post["pub_date"]:
                    past_context += f"【過去記事】タイトル: {past['title']}\n本文一部: {past['description'][:200]}...\n\n"
                    context_count += 1
                    if context_count > 3:
                        break

            # 5. 本文から画像URLを抽出
            img_urls = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', post["description"])
            contents = []
            
            # テーマがある場合とない場合でフォーマットの指示を切り替える
            format_target = f"{post['title']} {current_theme}" if current_theme else f"{post['title']}"
            
            # プロンプトの組み立て
            prompt_text = (
                f"あなたはアンジュルムの熱心なファンであり、優秀な広報アシスタントです。\n"
                f"以下の情報（今回のブログ、直近の文脈、もしあれば画像）をすべて分析した上で、指定のフォーマットの【超要約】を1つだけ作成してください。\n\n"
                f"■ メンバー名(テーマ): {current_theme if current_theme else '不明'}\n"
                f"■ 今回のブログタイトル: {post['title']}\n"
                f"■ 今回の本文: {post['description']}\n\n"
                f"■ 直近の過去記事の文脈:\n{past_context if past_context else '直近に過去投稿なし'}\n\n"
                f"【出力フォーマットの厳格なルール】\n"
                f"必ず以下の2行の形式だけで出力してください。挨拶、前置き、セクション名、解説などは一切出力しないでください。\n\n"
                f"読み手が満足感を感じるように、ブログにある内容や言及にはなるべく多く触れてください。"
                f"{format_target}\n"
                f"※【重要】要約の文頭や文末に「」や『』、丸括弧などの記号は絶対に付けないでください。文章だけで開始してください。\n"
                f"※【重要】メンバーの口調のまま表現する部分は「」書きで直接表現で、客観的にまとめた文章は「」なしの間接表現にしてください。また、間接表現はブログの雰囲気に合わせてファニーやエモーションな表現にしてください。
                f"※【重要】文字数は必ず50文字以内（厳守）にしてください。\n"
                f"※【厳禁】ブログ内の具体的な場所（聖地や撮影場所など）を特定・推測できる情報は絶対に記載禁止です。"
            )
            contents.append(prompt_text)

            if img_urls:
                target_img = img_urls[0]
                if target_img.startswith("//"):
                    target_img = "https:" + target_img
                try:
                    img_data = urllib.request.urlopen(target_img).read()
                    contents.append(types.Part.from_bytes(data=img_data, mime_type="image/jpeg"))
                except Exception as e:
                    print(f"[画像読み込み失敗] {e}")

            # Geminiで生成
            try:
                response = client_gemini.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=contents
                )
                result_text = response.text.strip()
                if result_text:
                    tweet_lines.append(result_text)
            except Exception as e:
                print(f"Gemini APIエラー: {e}")

    # 6. 【テスト用出力】Xには投稿せず、ログにだけ表示する
    if tweet_lines:
        summary_text = "\n\n".join(tweet_lines)
        final_tweet = f"{summary_text}\n\n#アンジュルム #アンジュルムブログ定期便"
        
        print("\n==============================================")
        print("★ [デバッグ確認用] もし本番なら以下の内容がXに投稿されます ★")
        print("==============================================")
        print(final_tweet)
        print("==============================================")
    else:
        print("過去24時間以内に新しいブログ投稿はありませんでした。")

if __name__ == "__main__":
    main()

