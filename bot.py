import os
import urllib.request
import urllib.parse
import json
import re
import xml.etree.ElementTree as ET
import random
import tempfile
import time
from datetime import datetime, timedelta, timezone
from google import genai
from google.genai import types
import tweepy

def send_line_message(message, image_urls=None):
    """LINE Messaging APIを使って自分のLINEへプッシュ通知を送る"""
    channel_access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    
    if not channel_access_token or not user_id:
        print("LINEの認証情報が設定されていないため、LINE通知をスキップします。")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {channel_access_token}"
    }
    
    # 1. まずテキストメッセージを送信
    payload_text = {
        "to": user_id,
        "messages": [{"type": "text", "text": message}]
    }
    
    try:
        data_text = json.dumps(payload_text).encode("utf-8")
        req = urllib.request.Request(url, data=data_text, headers=headers, method="POST")
        with urllib.request.urlopen(req) as res:
            if res.getcode() == 200:
                print("LINEへのテキスト通知が正常に成功しました！")
    except Exception as e:
        print(f"LINEテキスト通知エラー: {e}")

    # 2. 画像がある場合は最大4枚を別メッセージで送信
    if image_urls:
        for idx, img_url in enumerate(image_urls):
            payload_image = {
                "to": user_id,
                "messages": [
                    {
                        "type": "image",
                        "originalContentUrl": img_url,
                        "previewImageUrl": img_url
                    }
                ]
            }
            try:
                data_image = json.dumps(payload_image).encode("utf-8")
                req = urllib.request.Request(url, data=data_image, headers=headers, method="POST")
                with urllib.request.urlopen(req) as res:
                    if res.getcode() == 200:
                        print(f"LINEへの画像通知 ({idx+1}/{len(image_urls)}) が成功しました！")
            except Exception as e:
                print(f"LINE画像通知エラー: {e}")

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
    
    # 対象期間を実行日の「前日（00:00:00 〜 23:59:59）」に固定
    yesterday = now - timedelta(days=1)
    start_of_yesterday = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_yesterday = yesterday.replace(hour=23, minute=59, second=59, microsecond=0)
    
    print(f"現在時刻: {now.strftime('%Y-%m-%d %H:%M:%S')} (JST)")
    print(f"対象期間（前日限定）: {start_of_yesterday.strftime('%Y-%m-%d %H:%M:%S')} 〜 {end_of_yesterday.strftime('%Y-%m-%d %H:%M:%S')}\n")

    client_gemini = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    all_posts = []
    for item in items:
        title = item.find("title").text
        description = item.find("description").text
        link_url = item.find("link").text
        category_tag = item.find("category")
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
            "link_url": link_url,
            "pub_date": pub_date
        })

    processed_tweets_data = []
    # 画像選定のための情報保持用リスト
    extracted_images_info = []

    # ==========================================
    # 処理①：対象ブログをスキャンして要約と画像を抽出
    # ==========================================
    for post in all_posts:
        # 【重要】必ず前日期間内の記事のみを対象にする
        if start_of_yesterday <= post["pub_date"] <= end_of_yesterday:
            current_theme = post["theme"] if post["theme"] else "不明"
            print(f"【判定一致】処理を開始します: {current_theme} - {post['title']}")

            past_context = ""
            context_count = 1
            for past in all_posts:
                if post["theme"] and past["theme"] == post["theme"] and past["pub_date"] < post["pub_date"]:
                    past_context += f"【過去記事】タイトル: {past['title']}\n本文一部: {past['description'][:200]}...\n\n"
                    context_count += 1
                    if context_count > 3:
                        break

            # 文字列から直接アメブロ画像URLをすべて抽出
            corrected_img_urls = []
            raw_img_matches = re.findall(r'https://stat\.ameba\.jp/user_images/[^\s"\'<>]+', post["description"])
            
            for url in raw_img_matches:
                url = url.split('"')[0].split("'")[0].split('>')[0]
                if "charimages" in url or "blog_import" in url or url.lower().endswith(".gif"):
                    continue
                if url not in corrected_img_urls:
                    corrected_img_urls.append(url)

            print(f" -> 抽出された有効な写真（全枚数）: {len(corrected_img_urls)}枚")

            # 【修正】対象期間内の記事の画像のみを分類して蓄積
            for idx, url in enumerate(corrected_img_urls):
                if idx == 0:
                    extracted_images_info.append({"theme": current_theme, "type": "first", "url": url})
                else:
                    extracted_images_info.append({"theme": current_theme, "type": "sub", "url": url})

            # Geminiプロンプトの組み立て
            prompt_text = (
                f"あなたはアンジュルムの熱心なファンであり、優秀な広報アシスタントです。\n"
                f"指定のフォーマットの【超要約】を1つだけ作成してください。\n\n"
                f"■ メンバー名(テーマ): {current_theme}\n"
                f"■ 今回のブログタイトル: {post['title']}\n"
                f"■ 今回の本文: {post['description']}\n\n"
                f"■ 直近の過去記事の文脈:\n{past_context if past_context else '直近に過去投稿なし'}\n\n"
                f"【出力フォーマットと表現の厳格なルール】\n"
                f"1. 挨拶、タイトル等は一切出力せず、純粋な要約文（2〜3行程度）だけを出力してください。\n"
                f"2. ブログにある日常の出来事や感想などの内容を拾って構成してください。\n"
                f"3. 【最重要】ブログの最後によくある「ライブ、イベント、バースデーイベント、グッズ、TV・ラジオ出演」などの【告知情報・お知らせ】は要約に絶対に含めず、完全に無視してください。\n"
                f"4. 文章のなかに必ず指定のあだ名（れら、鈴ちゃん、しおんぬ、ケロ、わかにゃ、ゆきちゃん、ゆっぴょん、はなな、もち）を使ってメンバー名を書き入れ、主語を明確にしてください。\n"
                f"5. メンバーの口調のまま表現する部分は「」書きに、客観的なまとめは「」なしにしてください。\n"
                f"6. 【厳守】1人あたりの要約の全体の文字数は、必ず70文字以内（厳守）にしてください。\n"
                f"7. ブログ内の具体的な場所を特定・推測できる情報は絶対に記載禁止です。\n"
                f"8. 文頭に、メンバーを表す絵文字を入れてください（れら→🦐、鈴ちゃん→🔔、しおんぬ→🎶、ケロ→🐸、ゆきちゃん→❄、わかにゃ→🍞、ゆっぴょん→🐰、はなな→🌼、もち→🎨）。"
            )
            
            contents = [prompt_text]

            if corrected_img_urls:
                try:
                    req = urllib.request.Request(corrected_img_urls[0], headers={'User-Agent': 'Mozilla/5.0'})
                    img_data = urllib.request.urlopen(req).read()
                    contents.append(types.Part.from_bytes(data=img_data, mime_type="image/jpeg"))
                except Exception as e:
                    print(f"[Gemini用画像読み込み失敗] {e}")

            try:
                response = client_gemini.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=contents
                )
                result_text = response.text.strip()
                if result_text:
                    processed_tweets_data.append(result_text)
            except Exception as e:
                print(f"Gemini APIエラー: {e}")

    # ==========================================
    # 処理②：画像の高度な選定ロジック（最大4枚）
    # ==========================================
    selected_images_objects = []
    
    if processed_tweets_data:
        # 1. まずベースとして、対象ブログの「2枚目以降の画像（sub）」を取得
        sub_images = [img for img in extracted_images_info if img["type"] == "sub"]
        
        random.shuffle(sub_images)
        for img in sub_images:
            if len(selected_images_objects) < 4:
                if img["url"] not in [x["url"] for x in selected_images_objects]:
                    selected_images_objects.append(img)

        # 2. 4枚に満たない場合、1枚目の画像（first）から補填
        if len(selected_images_objects) < 4:
            print(f"-> 2枚目以降の画像だけでは{len(selected_images_objects)}枚のため、1枚目の画像から補填を行います。")
            first_images = [img for img in extracted_images_info if img["type"] == "first"]
            
            # すでに選ばれているメンバー（テーマ）のリスト
            already_chosen_themes = set([img["theme"] for img in selected_images_objects])
            
            # 優先度A: 「まだ写真（2枚目以降）が1枚も選ばれていないメンバー」の1枚目
            priority_a = [img for img in first_images if img["theme"] not in already_chosen_themes]
            random.shuffle(priority_a)
            
            # 優先度B: 「すでに選ばれているメンバー」の1枚目
            priority_b = [img for img in first_images if img["theme"] in already_chosen_themes]
            random.shuffle(priority_b)
            
            fill_candidates = priority_a + priority_b
            
            for img in fill_candidates:
                if len(selected_images_objects) < 4:
                    if img["url"] not in [x["url"] for x in selected_images_objects]:
                        selected_images_objects.append(img)
                        print(f"   [補填採用] メンバー: {img['theme']} の1枚目の写真を追加しました。")

        final_image_urls = [img["url"] for img in selected_images_objects]
        print(f"最終投稿写真（合計 {len(final_image_urls)} 枚）が確定しました。")

        # ==========================================
        # 処理③：一括でXとLINEに投稿
        # ==========================================
        auth = tweepy.OAuth1UserHandler(
            os.environ.get("TWITTER_API_KEY"),
            os.environ.get("TWITTER_API_SECRET"),
            os.environ.get("TWITTER_ACCESS_TOKEN"),
            os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
        )
        api_v1 = tweepy.API(auth)
        
        client_x = tweepy.Client(
            consumer_key=os.environ.get("TWITTER_API_KEY"),
            consumer_secret=os.environ.get("TWITTER_API_SECRET"),
            access_token=os.environ.get("TWITTER_ACCESS_TOKEN"),
            access_token_secret=os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
        )

        media_ids = []
        temp_files = []

        for idx, img_url in enumerate(final_image_urls):
            try:
                print(f"X用画像ダウンロード中 ({idx+1}/{len(final_image_urls)}): {img_url}")
                req = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
                img_data = urllib.request.urlopen(req).read()
                
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                temp_file.write(img_data)
                temp_file.close()
                temp_files.append(temp_file.name)
                
                media = api_v1.media_upload(filename=temp_file.name)
                media_ids.append(media.media_id_string)
            except Exception as img_err:
                print(f"X用画像アップロード失敗 ({img_url}): {img_err}")

        summary_text = "\n\n".join(processed_tweets_data)
        time_str = start_of_yesterday.strftime('%Y/%m/%d')
        
        # 【修正】個人のリンクではなく、一覧ページのみを出力
        final_tweet = (
            f"#アンジュルムブログ定期便🪽\n"
            f"{time_str} ※忙しい人向けブログ要約です👍\n\n"
            f"{summary_text}\n\n"
            f"🔗 一覧: https://ameblo.jp/angerme-new/"
        )
        
        print("\n[本番投稿内容の確認]")
        print(final_tweet)
        
        try:
            if media_ids:
                client_x.create_tweet(text=final_tweet, media_ids=media_ids)
            else:
                client_x.create_tweet(text=final_tweet)
            print("X（Twitter）への本番投稿が正常に成功しました！")
            
        except Exception as e:
            print(f"X（Twitter）投稿エラー: {e}")
        finally:
            for path in temp_files:
                if os.path.exists(path):
                    os.remove(path)
            
        line_message = f"\n【X投稿内容】\n{final_tweet}"
        send_line_message(line_message, image_urls=final_image_urls)
        
    else:
        print("対象期間（前日）内に新しいブログ投稿がRSSに存在しなかったため、処理をスキップしました。")

if __name__ == "__main__":
    main()

