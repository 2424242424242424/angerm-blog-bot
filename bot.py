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
    
    # 【修正】対象期間を実行日の「前日（00:00:00 〜 23:59:59）」に厳格化
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
            "pub_date": pub_date
        })

    processed_tweets_data = []
    pool_images = []

    # ==========================================
    # 処理①：対象ブログをスキャンして要約と画像を抽出
    # ==========================================
    for post in all_posts:
        # 前日（昨日）の投稿のみを対象とする
        if start_of_yesterday <= post["pub_date"] <= end_of_yesterday:
            current_theme = post["theme"]
            print(f"【判定一致】処理を開始します: {current_theme} - {post['title']}")

            past_context = ""
            context_count = 1
            for past in all_posts:
                if current_theme and past["theme"] == current_theme and past["pub_date"] < post["pub_date"]:
                    past_context += f"【過去記事】タイトル: {past['title']}\n本文一部: {past['description'][:200]}...\n\n"
                    context_count += 1
                    if context_count > 3:
                        break

            # 【修正】アメブロの画像URL（stat.ameba.jp）を確実に、重複なく抽出する正規表現
            corrected_img_urls = []
            raw_img_matches = re.findall(r'src=["\'](https?://stat\.ameba\.jp/user_images/[^"\']+)["\']', post["description"])
            
            for url in raw_img_matches:
                # スタンプや絵文字などのアイコンは除外
                if "charimages" in url or "blog_import" in url or url.endswith(".gif"):
                    continue
                if url not in corrected_img_urls:
                    corrected_img_urls.append(url)

            print(f" -> 抽出された有効な写真（全枚数）: {len(corrected_img_urls)}枚")

            # 1枚しかない場合はプールしない（添付しない）。2枚目以降がある場合のみ蓄積
            if len(corrected_img_urls) > 1:
                pool_images.extend(corrected_img_urls[1:])

            # Geminiプロンプトの組み立て
            prompt_text = (
                f"あなたはアンジュルムの熱心なファンであり、優秀な広報アシスタントです。\n"
                f"指定のフォーマットの【超要約】を1つだけ作成してください。\n\n"
                f"■ メンバー名(テーマ): {current_theme if current_theme else '不明'}\n"
                f"■ 今回のブログタイトル: {post['title']}\n"
                f"■ 今回の本文: {post['description']}\n\n"
                f"■ 直近の過去記事の文脈:\n{past_context if past_context else '直近に過去投稿なし'}\n\n"
                f"【出力フォーマットと表現の厳格なルール】\n"
                f"1. 挨拶、タイトル等は一切出力せず、純粋な要約文（2〜3行程度）だけを出力してください。\n"
                f"2. 読み手が満足感を感じるように、ブログにある日常の出来事や感想などの内容にはなるべく多く触れてください。\n"
                f"3. 【重要】ブログの最後などによくある「ライブ、イベント、バースデーイベント、グッズ、TV出演」などの【告知情報・お知らせ】は絶対に要約に含めず、完全に無視してください。\n"
                f"4. 文章のなかに必ず指定のあだ名（れら、鈴ちゃん、しおんぬ、ケロ、わかにゃ、ゆきちゃん、ゆっぴょん、はなな、もち）を使ってメンバー名を書き入れ、主語を明確にしてください。\n"
                f"5. メンバーの口調のまま表現する部分は「」書きに、客観的なまとめは「」なしにしてください。\n"
                f"6. 全体の文字数は必ず70文字以内（厳守）にしてください。\n"
                f"7. ブログ内の具体的な場所を特定・推測できる情報は絶対に記載禁止です。\n"
                f"8. 文頭に、メンバーを表す絵文字を入れてください（れら→🦐、鈴ちゃん→🔔、しおんぬ→🎶、ケロ→🐸、ゆきちゃん→❄、わかにゃ→🍞、ゆっぴょん→🐰、はなな→🌼、もち→🎨）。"
            )
            
            contents = [prompt_text]

            # 1枚目の画像はGeminiの認識用にセット
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
    # 処理②：すべて終わった後、一括でXとLINEに投稿
    # ==========================================
    if processed_tweets_data:
        unique_pool_images = list(set(pool_images))
        selected_images = random.sample(unique_pool_images, min(len(unique_pool_images), 4)) if unique_pool_images else []
        print(f"確定した2枚目以降の写真総数: {len(unique_pool_images)}枚 -> ランダム選択された数: {len(selected_images)}枚")

        # X（Twitter）の認証
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

        for idx, img_url in enumerate(selected_images):
            try:
                print(f"X用画像ダウンロード中 ({idx+1}/{len(selected_images)}): {img_url}")
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
        final_tweet = f"#アンジュルムブログ定期便🪽\n{time_str} ※忙しい人向けブログ要約です👍\n\n{summary_text}"
        
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
        send_line_message(line_message, image_urls=selected_images)
        
    else:
        print("対象期間（前日）内に新しいブログ投稿がRSSに存在しなかったため、処理をスキップしました。")

if __name__ == "__main__":
    main()
