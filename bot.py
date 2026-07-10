import os
import urllib.request
import urllib.parse
import json
import re
import xml.etree.ElementTree as ET
import random
import tempfile
from datetime import datetime, timedelta, timezone
from google import genai
from google.genai import types
import tweepy

def send_line_message(message, image_urls=None):
    """
    LINE Messaging APIを使って自分のLINEへプッシュ通知を送る。
    画像がある場合は、テキスト通知の後に画像メッセージも追加で送信する。
    """
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
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }
    
    try:
        data_text = json.dumps(payload_text).encode("utf-8")
        req = urllib.request.Request(url, data=data_text, headers=headers, method="POST")
        with urllib.request.urlopen(req) as res:
            if res.getcode() == 200:
                print("LINEへのテキスト通知が正常に成功しました！")
            else:
                print(f"LINEテキスト通知に失敗しました。ステータスコード: {res.getcode()}")
    except Exception as e:
        print(f"LINEテキスト通知エラー: {e}")

    # 2. 画像が指定されている場合、画像メッセージを1枚ずつ送信
    if image_urls:
        for idx, img_url in enumerate(image_urls):
            # LINEの仕様により、送信する画像URLとプレビュー用URL(同一でも可)が必要
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
                    else:
                        print(f"LINE画像通知に失敗しました。ステータスコード: {res.getcode()}")
            except Exception as e:
                print(f"LINE画像通知エラー ({img_url}): {e}")

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
    
    # 昨日の日付の 00:00:00 〜 23:59:59 の範囲を作成
    yesterday = now - timedelta(days=1)
    start_of_yesterday = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_yesterday = yesterday.replace(hour=23, minute=59, second=59, microsecond=0)
    
    print(f"現在時刻: {now.strftime('%Y-%m-%d %H:%M:%S')} (JST)")
    print(f"【本番モード】対象期間: {start_of_yesterday.strftime('%Y-%m-%d %H:%M:%S')} 〜 {end_of_yesterday.strftime('%Y-%m-%d %H:%M:%S')} の記事を対象にします。\n")

    client_gemini = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    # 全記事をパースしてリスト化
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

    tweet_lines = []
    pool_images = []  # ランダム投稿用に「各記事の2枚目以降の画像」を溜めるリスト

    # 3. 各記事をループ（昨日の記事だけを特定）
    for post in all_posts:
        if start_of_yesterday <= post["pub_date"] <= end_of_yesterday:
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
            
            # 各ブログの「1枚目以外（2枚目以降）」の画像をランダム用プールに追加
            if len(img_urls) > 1:
                for img_url in img_urls[1:]:
                    if img_url.startswith("//"):
                        img_url = "https:" + img_url
                    pool_images.append(img_url)

            contents = []
            
            # プロンプトの組み立て
            prompt_text = (
                f"あなたはアンジュルムの熱心なファンであり、優秀な広報アシスタントです。\n"
                f"以下の情報（今回のブログ、直近の文脈、もしあれば画像）をすべて分析した上で、指定のフォーマットの【超要約】を1つだけ作成してください。\n\n"
                f"■ メンバー名(テーマ): {current_theme if current_theme else '不明'}\n"
                f"■ 今回のブログタイトル: {post['title']}\n"
                f"■ 今回の本文: {post['description']}\n\n"
                f"■ 直近の過去記事の文脈:\n{past_context if past_context else '直近に過去投稿なし'}\n\n"
                f"【出力フォーマットと表現の厳格なルール】\n"
                f"1. 挨拶、前置き、ブログタイトル、セクション名、末尾の定型ブロックなどは一切出力せず、告知情報はなるべく出力せず、純粋な要約文（2〜3行程度）だけを出力してください。\n"
                f"2. 読み手が満足感を感じるように、ブログにある内容や言及にはなるべく多く触れてください。\n"
                f"3. 文章のなかに、必ず誰が話している内容か分かるように、以下の【指定のあだ名】を使ってメンバー名を書き入れ、主語を明確にしてください。\n"
                f"   【指定のあだ名ルール】\n"
                f"   ・伊勢鈴蘭 → れら\n"
                f"   ・橋迫鈴 → 鈴ちゃん\n"
                f"   ・為永幸音 → しおんぬ\n"
                f"   ・川名凜 → ケロ\n"
                f"   ・松本わかな → わかにゃ\n"
                f"   ・平山遊季 →ゆきちゃん\n"
                f"   ・下井谷幸穂 → ゆっぴょん\n"
                f"   ・後藤花 → はなな\n"
                f"   ・長野桃羽 → もち\n"
                f"4. メンバーの口調のまま表現する部分は「」書きで直接表現に、客観的にまとめた文章は「」なしの間接表現にしてください。また、間接表現はブログの雰囲気に合わせてファニーやエモーションな表現にしてください。\n"
                f"5. 要約全体の文頭や文末に「」や『』、丸括弧などの記号は絶対に付けないでください。文章だけで開始してください。\n"
                f"6. 全体の文字数は必ず70文字以内（厳守）にしてください。\n"
                f"7. 【厳禁】ブログ内の具体的な場所（聖地や撮影場所など）を特定・推測できる情報は絶対に記載禁止です。\n"
                f"8. 段落の頭に、メンバーを表す絵文字を入れてください。れら→🦐、鈴ちゃん→🔔、しおんぬ→🎶、ケロ→🐸、ゆきちゃん→❄、わかにゃ→🍞、ゆっぴょん→🐰、はなな→🌼、もち→🎨"
            )

            contents.append(prompt_text)

            # Geminiの認識用には従来通り1枚目の画像を送る
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

    # 6. 新着投稿があれば、1つのポストにまとめてX（Twitter）へ自動投稿
    if tweet_lines:
        summary_text = "\n\n".join(tweet_lines)
        
        yesterday = now - timedelta(days=1)
        time_str = yesterday.strftime('%Y/%m/%d')
        
        final_tweet = f"#アンジュルムブログ定期便🪽\n{time_str} ※忙しい人向けブログ要約です👍\n\n{summary_text}"
        
        print("\n[本番投稿内容の確認]")
        print(final_tweet)
        
        # --- プールのなかから最大4枚をランダム抽出 ---
        media_ids = []
        temp_files = []
        
        selected_images = random.sample(pool_images, min(len(pool_images), 4)) if pool_images else []
        
        try:
            # X（Twitter）の画像アップロード認証
            auth = tweepy.OAuth1UserHandler(
                os.environ.get("TWITTER_API_KEY"),
                os.environ.get("TWITTER_API_SECRET"),
                os.environ.get("TWITTER_ACCESS_TOKEN"),
                os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
            )
            api_v1 = tweepy.API(auth)
            
            # 選ばれた画像をローカルに落としてXにアップロード
            for idx, img_url in enumerate(selected_images):
                try:
                    print(f"画像ダウンロード中 ({idx+1}/4): {img_url}")
                    img_data = urllib.request.urlopen(img_url).read()
                    
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                    temp_file.write(img_data)
                    temp_file.close()
                    temp_files.append(temp_file.name)
                    
                    media = api_v1.media_upload(filename=temp_file.name)
                    media_ids.append(media.media_id_string)
                except Exception as img_err:
                    print(f"画像処理エラー ({img_url}): {img_err}")

            # X（Twitter）への投稿（v2）
            client_x = tweepy.Client(
                consumer_key=os.environ.get("TWITTER_API_KEY"),
                consumer_secret=os.environ.get("TWITTER_API_SECRET"),
                access_token=os.environ.get("TWITTER_ACCESS_TOKEN"),
                access_token_secret=os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
            )
            
            if media_ids:
                client_x.create_tweet(text=final_tweet, media_ids=media_ids)
            else:
                client_x.create_tweet(text=final_tweet)
                
            print("X（Twitter）への本番投稿が正常に成功しました！")
            
        except Exception as e:
            print(f"X（Twitter）投稿エラー: {e}")
        finally:
            # 作成した一時ファイルを削除してクリーンアップ
            for path in temp_files:
                if os.path.exists(path):
                    os.remove(path)
            
        # 【修正】Xへの投稿が動いた後、同じ内容と選ばれた画像リストをLINEにも通知する
        line_message = f"\n【X投稿内容】\n{final_tweet}"
        send_line_message(line_message, image_urls=selected_images)
        
    else:
        print("過去24時間以内に新しいブログ投稿はなかったため、投稿をスキップしました。")

if __name__ == "__main__":
    main()
