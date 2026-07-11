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
    
    # テキストメッセージ送信
    payload_text = {"to": user_id, "messages": [{"type": "text", "text": message}]}
    try:
        data_text = json.dumps(payload_text).encode("utf-8")
        req = urllib.request.Request(url, data=data_text, headers=headers, method="POST")
        with urllib.request.urlopen(req) as res:
            if res.getcode() == 200:
                print("LINEへのテキスト通知が成功しました！")
    except Exception as e:
        print(f"LINEテキスト通知エラー: {e}")

    # 画像通知（LINEは最大枚数制限が厳しいため、全URLを羅列するか、必要に応じて分割送信）
    if image_urls:
        for idx, img_url in enumerate(image_urls):
            # LINEの仕様上、imageタイプは1メッセージ1画像
            payload_image = {
                "to": user_id,
                "messages": [{"type": "image", "originalContentUrl": img_url, "previewImageUrl": img_url}]
            }
            try:
                data_image = json.dumps(payload_image).encode("utf-8")
                req = urllib.request.Request(url, data=data_image, headers=headers, method="POST")
                with urllib.request.urlopen(req) as res:
                    if res.getcode() == 200:
                        print(f"LINE画像通知 ({idx+1}) 成功")
            except Exception as e:
                print(f"LINE画像通知エラー: {e}")

def main():
    rss_url = "https://rssblog.ameba.jp/angerme-new/rss20.xml"
    response = urllib.request.urlopen(rss_url)
    xml_data = response.read()
    root = ET.fromstring(xml_data)
    items = root.findall(".//item")
    
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    yesterday = now - timedelta(days=1)
    start_of_yesterday = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_yesterday = yesterday.replace(hour=23, minute=59, second=59, microsecond=0)

    client_gemini = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    all_posts = []
    for item in items:
        pub_date_str = item.find("pubDate").text
        pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %z")
        if start_of_yesterday <= pub_date <= end_of_yesterday:
            all_posts.append({
                "theme": item.find("category").text if item.find("category") is not None else "不明",
                "title": item.find("title").text,
                "description": item.find("description").text,
                "pub_date": pub_date
            })

    if not all_posts:
        print("対象期間内に新しい投稿なし。")
        return

    processed_tweets_data = []
    all_extracted_image_urls = []

    # 1. 記事要約と画像抽出
    for post in all_posts:
        # 画像抽出
        raw_img_matches = re.findall(r'https://stat\.ameba\.jp/user_images/[^\s"\'<>]+', post["description"])
        for url in raw_img_matches:
            url = url.split('"')[0].split("'")[0].split('>')[0]
            if "charimages" not in url and "blog_import" not in url and not url.lower().endswith(".gif"):
                if url not in all_extracted_image_urls:
                    all_extracted_image_urls.append(url)

        # 要約作成
        prompt = f"要約してください：{post['title']}\n本文：{post['description'][:500]}..."
        try:
            res = client_gemini.models.generate_content(model='gemini-2.5-flash', contents=[prompt])
            processed_tweets_data.append(res.text.strip())
        except Exception as e:
            print(f"Geminiエラー: {e}")

    # 2. X投稿処理
    auth = tweepy.OAuth1UserHandler(
        os.environ.get("TWITTER_API_KEY"), os.environ.get("TWITTER_API_SECRET"),
        os.environ.get("TWITTER_ACCESS_TOKEN"), os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
    )
    api_v1 = tweepy.API(auth)
    client_x = tweepy.Client(
        consumer_key=os.environ.get("TWITTER_API_KEY"), consumer_secret=os.environ.get("TWITTER_API_SECRET"),
        access_token=os.environ.get("TWITTER_ACCESS_TOKEN"), access_token_secret=os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
    )

    # 全画像をアップロードしてメディアIDを取得
    all_media_ids = []
    temp_files = []
    for img_url in all_extracted_image_urls:
        try:
            req = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
            img_data = urllib.request.urlopen(req).read()
            t = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            t.write(img_data)
            t.close()
            temp_files.append(t.name)
            media = api_v1.media_upload(filename=t.name)
            all_media_ids.append(media.media_id_string)
        except Exception as e:
            print(f"画像アップロード失敗: {e}")

    # 親投稿の作成
    summary_text = "\n\n".join(processed_tweets_data)
    final_tweet = f"#アンジュルムブログ定期便🪽\n{start_of_yesterday.strftime('%Y/%m/%d')}\n\n{summary_text}\n\n🔗一覧: https://ameblo.jp/angerme-new/"
    
    # 最初の4枚を親投稿に添付
    parent_media_ids = all_media_ids[:4]
    response_tweet = client_x.create_tweet(text=final_tweet, media_ids=parent_media_ids if parent_media_ids else None)
    parent_tweet_id = response_tweet.data["id"]

    # 5枚目以降を返信ツリーにする
    reply_media_ids_list = [all_media_ids[i:i + 4] for i in range(4, len(all_media_ids), 4)]
    reply_target_id = parent_tweet_id
    
    for i, media_group in enumerate(reply_media_ids_list):
        res_reply = client_x.create_tweet(
            text=f"📸 ブログ写真まとめ ({i+1}/{len(reply_media_ids_list)})",
            media_ids=media_group,
            in_reply_to_tweet_id=reply_target_id
        )
        reply_target_id = res_reply.data["id"]
        time.sleep(2)

    # クリーンアップ
    for path in temp_files:
        if os.path.exists(path): os.remove(path)

    # LINE通知
    send_line_message(f"【X投稿完了】\n{final_tweet}", image_urls=all_extracted_image_urls)

if __name__ == "__main__":
    main()
