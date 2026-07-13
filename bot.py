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

# ★テスト設定：ここを True にするとX投稿をスキップし、LINE通知のみ行います
IS_TEST_MODE = True

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

    # 2. 画像がある場合は最大4枚ずつ別メッセージで送信
    if image_urls:
        for idx in range(0, len(image_urls), 4):
            chunk = image_urls[idx:idx+4]
            messages = []
            for img_url in chunk:
                messages.append({
                    "type": "image",
                    "originalContentUrl": img_url,
                    "previewImageUrl": img_url
                })
            payload_image = {
                "to": user_id,
                "messages": messages
            }
            try:
                data_image = json.dumps(payload_image).encode("utf-8")
                req = urllib.request.Request(url, data=data_image, headers=headers, method="POST")
                with urllib.request.urlopen(req) as res:
                    if res.getcode() == 200:
                        print(f"LINEへの画像通知 ({idx+1}〜{idx+len(chunk)}枚目) が成功しました！")
            except Exception as e:
                print(f"LINE画像通知エラー: {e}")

def main():
    # 1. 各グループのRSS URLリスト
    rss_urls = {
        "angerme": "https://rssblog.ameba.jp/angerme-new/rss20.xml",
        "angerme-ss-shin": "https://rssblog.ameba.jp/angerme-ss-shin/rss20.xml",
        "morningmusume_15ki": "https://rssblog.ameba.jp/morningmusume15ki/rss20.xml",
        "morningmusume_16ki": "https://rssblog.ameba.jp/morningmusume16ki/rss20.xml",
        "morningmusume_10ki": "https://rssblog.ameba.jp/morningmusume-10ki/rss20.xml",
        "morningmusume_12ki": "https://rssblog.ameba.jp/morningmusume-12ki/rss20.xml",
        "juicejuice": "https://rssblog.ameba.jp/juicejuice-official/rss20.xml",
        "inaba-manaka": "https://rssblog.ameba.jp/inaba-manaka/rss20.xml",
        "tsubaki_factory_old": "https://rssblog.ameba.jp/tsubaki-factory/rss20.xml",
        "tsubaki_factory_new": "https://rssblog.ameba.jp/tsubaki-factory-new/rss20.xml",
        "beyooooonds_chicatetsu": "https://rssblog.ameba.jp/beyooooonds-chicatetsu/rss20.xml",
        "beyooooonds_rfro": "https://rssblog.ameba.jp/beyooooonds-rfro/rss20.xml",
        "beyooooonds_seasonings": "https://rssblog.ameba.jp/beyooooonds/rss20.xml",
        "beyooooonds_noname": "https://rssblog.ameba.jp/beyooooonds-blog/rss20.xml",
        "ocha_norma": "https://rssblog.ameba.jp/ocha-norma/rss20.xml",
        "rosychronicle": "https://rssblog.ameba.jp/rosychronicle/rss20.xml"
    }

    # アンジュルム言及を一次検知するためのキーワード
    angerme_keywords = (
        r"アンジュルム|アンジュ|スマイレージ|スマ|"
        r"上國料|かみこ|萌衣|川村|文乃|かわむー|かむ|伊勢|鈴蘭|れいら|れら|れらたん|橋迫|鈴|鈴ちゃん|"
        r"川名|凜|ケロ|ケロちゃん|為永|幸音|しおんぬ|ため|松本|わかな|わかにゃ|平山|遊季|ゆき|ぺいぺい|ぺい|"
        r"下井谷|幸穂|ゆっぴょん|ゆきほ|後藤|花|はな|はなな|ごっちん|長野|桃羽|もっち|もち|ももは"
    )
    
    # 2. 基準時刻の設定 (JST基準)
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    
    yesterday = now - timedelta(days=1)
    start_of_yesterday = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_yesterday = yesterday.replace(hour=23, minute=59, second=59, microsecond=0)
    
    print(f"現在時刻: {now.strftime('%Y-%m-%d %H:%M:%S')} (JST)")
    print(f"対象期間（前日限定）: {start_of_yesterday.strftime('%Y-%m-%d %H:%M:%S')} 〜 {end_of_yesterday.strftime('%Y-%m-%d %H:%M:%S')}\n")

    client_gemini = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    processed_tweets_data = []      # アンジュルム本人の要約
    mention_tweets_data = []        # 他グループからの言及要約
    all_extracted_image_urls = []   # すべての画像URL（一括アップロード用）

    # ==========================================
    # 処理①：各グループのブログRSSを巡回・スキャン
    # ==========================================
    for group_key, rss_url in rss_urls.items():
        print(f"【RSSスキャン中】: {group_key}")
        try:
            req_rss = urllib.request.Request(rss_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_rss) as response:
                xml_data = response.read()
            root = ET.fromstring(xml_data)
            items = root.findall(".//item")
        except Exception as e:
            print(f" -> RSS取得失敗 ({group_key}): {e}")
            continue

        for item in items:
            title = item.find("title").text if item.find("title") is not None else ""
            description = item.find("description").text if item.find("description") is not None else ""
            link_url = item.find("link").text if item.find("link") is not None else ""
            category_tag = item.find("category")
            theme = category_tag.text if category_tag is not None else "不明"
            
            pub_date_str = item.find("pubDate").text
            try:
                pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %z")
            except ValueError:
                continue
            
            if not (start_of_yesterday <= pub_date <= end_of_yesterday):
                continue

            # --- アンジュルム公式ブログの場合の処理 ---
            if group_key in ["angerme", "angerme-ss-shin"]:
                print(f" -> 【アンジュルム本日判定一致】: {theme} - {title}")
                
                corrected_img_urls = []
                raw_img_matches = re.findall(r'https://stat\.ameba\.jp/user_images/[^\s"\'<>]+', description)
                for url in raw_img_matches:
                    url = url.split('"')[0].split("'")[0].split('>')[0]
                    if "charimages" in url or "blog_import" in url or url.lower().endswith(".gif"):
                        continue
                    if url not in corrected_img_urls:
                        corrected_img_urls.append(url)
                
                for url in corrected_img_urls:
                    if url not in all_extracted_image_urls:
                        all_extracted_image_urls.append(url)

                prompt_text = (
                    f"あなたはアンジュルムの熱心なファンであり、優秀な広報アシスタントです。\n"
                    f"指定のフォーマットの【超要約】を1つだけ作成してください。\n\n"
                    f"■ メンバー名(テーマ): {theme}\n"
                    f"■ 今回のブログタイトル: {title}\n"
                    f"■ 今回の本文: {description}\n\n"
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
                        req_img = urllib.request.Request(corrected_img_urls[0], headers={'User-Agent': 'Mozilla/5.0'})
                        img_data = urllib.request.urlopen(req_img).read()
                        contents.append(types.Part.from_bytes(data=img_data, mime_type="image/jpeg"))
                    except Exception as e:
                        print(f"   [Gemini用画像読み込み失敗] {e}")

                try:
                    response = client_gemini.models.generate_content(model='gemini-2.5-flash', contents=contents)
                    result_text = response.text.strip()
                    if result_text: processed_tweets_data.append(result_text)
                except Exception as e:
                    print(f"   Gemini APIエラー: {e}")

            # --- 他のハロプロブログの場合の処理（アンジュルム言及チェック） ---
            else:
                if re.search(angerme_keywords, title) or re.search(angerme_keywords, description):
                    # 全画像URLを一旦パース
                    corrected_img_urls = []
                    raw_img_matches = re.findall(r'https://stat\.ameba\.jp/user_images/[^\s"\'<>]+', description)
                    for url in raw_img_matches:
                        url = url.split('"')[0].split("'")[0].split('>')[0]
                        if "charimages" in url or "blog_import" in url or url.lower().endswith(".gif"):
                            continue
                        if url not in corrected_img_urls:
                            corrected_img_urls.append(url)

                    # 他メン言及用の厳格なGeminiプロンプト（誤判定防止を極限まで強化）
                    prompt_mention = (
                        f"あなたはハロー！プロジェクトの熱心なファンであり、優秀な広報アシスタントです。\n"
                        f"提供されたブログの文章を解析し、【アンジュルムの現役メンバー】または【アンジュルムというグループ自体】に対する具体的な言及・交流（エピソード、会話、ツーショット等）が含まれている場合のみ、指定のフォーマットで1点要約を作成してください。\n\n"
                        f"【最重要：除外ルール】\n"
                        f"・他グループのメンバー（例：Juice=Juice、つばきファクトリー、BEYOOOOONDS、OCHA NORMA、ロージークロニクル等、アンジュルムではないメンバー）への言及しか見つからない場合は、アンジュルムへの言及とはみなさず、絶対に何も出力しないでください。\n"
                        f"・研修生同期であっても、相手が現役のアンジュルムメンバーでない場合は完全に無視してください。\n"
                        f"・「アンジュルムに関する言及はありません」などの言い訳・説明のテキストも一切出力禁止です。非該当の場合は完全に【空白（空文字）】で返してください。\n\n"
                        f"■ 投稿者グループ: {group_key}\n"
                        f"■ 投稿者名(テーマ): {theme}\n"
                        f"■ ブログタイトル: {title}\n"
                        f"■ 本文: {description}\n\n"
                        f"【出力フォーマット】※該当する場合のみ\n"
                        f"1. 挨拶、タイトル、前置き等は一切出力せず、純粋な要約文だけを出力してください。\n"
                        f"2. 「誰がアンジュルムの誰と何をしていたか、どんな交流・言及があったか」のコアな部分をエモーショナルに書いてください。\n"
                        f"3. 文章の最後に、ブログのURL（ {link_url} ）を必ず添えてください。\n"
                        f"4. 【厳守】全体の文字数は、URLを除いて必ず70文字以内（厳守）にしてください。\n"
                        f"5. 文頭には「💬 [グループ名略称・メンバー名]」という形式を記載してください。(例: 💬 [娘。小田]、💬 [Juice段原] )"
                    )

                    try:
                        response = client_gemini.models.generate_content(model='gemini-2.5-flash', contents=[prompt_mention])
                        result_text = response.text.strip()
                    except Exception as e:
                        print(f"   Gemini APIエラー(テキスト判定): {e}")
                        result_text = ""

                    # 判定の結果、正しくアンジュルムへの言及があった場合のみ写真精査と要約追加を行う
                    if result_text and not any(msg in result_text for msg in ["言及はありません", "表示不要", "対象外"]):
                        print(f" -> 【他グループ言及確定】[{group_key}] {theme} - {title}")
                        mention_tweets_data.append(result_text)

                        # 写真の選別：各画像をGeminiに見せて「アンジュルムメンバーがいるか」を1枚ずつ厳密に判定
                        for img_url in corrected_img_urls:
                            photo_prompt = (
                                "この画像に「アンジュルム（旧スマイレージ）」の現役メンバー、またはグループ全体のいずれかが写っていますか？\n"
                                "写っている場合は『YES』、他グループのメンバーしか写っていない場合や、アンジュルムのメンバーが写っていない場合は『NO』とだけ出力してください。解説は不要です。"
                            )
                            try:
                                req_img = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
                                img_data = urllib.request.urlopen(req_img).read()
                                photo_contents = [
                                    photo_prompt,
                                    types.Part.from_bytes(data=img_data, mime_type="image/jpeg")
                                ]
                                photo_res = client_gemini.models.generate_content(model='gemini-2.5-flash', contents=photo_contents)
                                decision = photo_res.text.strip().upper()
                                
                                if "YES" in decision:
                                    print(f"   [写真選別:合致] アンジュメンバーを検知したため写真を追加します: {img_url}")
                                    if img_url not in all_extracted_image_urls:
                                        all_extracted_image_urls.append(img_url)
                                else:
                                    print(f"   [写真選別:除外] アンジュメンバーが写っていないためスキップ: {img_url}")
                            except Exception as img_err:
                                print(f"   [写真判定エラー] {img_err}")

    # ==========================================
    # 処理②：一括でXとLINEに投稿
    # ==========================================
    if processed_tweets_data or mention_tweets_data:
        summary_text = "\n\n".join(processed_tweets_data) if processed_tweets_data else "（本日の新規ブログ投稿はありません）"
        time_str = start_of_yesterday.strftime('%Y/%m/%d')
        
        final_tweet = (
            f"#アンジュルムブログ定期便🪽\n"
            f"{time_str} ※忙しい人向けブログ要約です👍\n\n"
            f"{summary_text}\n\n"
            f"🔗 一覧: https://ameblo.jp/angerme-new/"
        )
        
        if mention_tweets_data:
            mention_text = "\n\n".join(mention_tweets_data)
            final_tweet += f"\n\nーーー\n✉️他のハロメンやOGより\n\n{mention_text}"
        
        print("\n[投稿内容の確認]")
        print(final_tweet)
        
        if not IS_TEST_MODE:
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

            all_media_ids = []
            temp_files = []

            print(f"\n[画像アップロード開始] 合計 {len(all_extracted_image_urls)} 枚の処理中...")
            for idx, img_url in enumerate(all_extracted_image_urls):
                try:
                    req_img = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
                    img_data = urllib.request.urlopen(req_img).read()
                    
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                    temp_file.write(img_data)
                    temp_file.close()
                    temp_files.append(temp_file.name)
                    
                    media = api_v1.media_upload(filename=temp_file.name)
                    all_media_ids.append(media.media_id_string)
                    print(f" -> アップロード成功 ({idx+1}/{len(all_extracted_image_urls)}): {img_url}")
                except Exception as img_err:
                    print(f" -> アップロード失敗 ({img_url}): {img_err}")
            
            parent_tweet_id = None
            try:
                parent_media_ids = all_media_ids[:4]
                if parent_media_ids:
                    response_tweet = client_x.create_tweet(text=final_tweet, media_ids=parent_media_ids)
                else:
                    response_tweet = client_x.create_tweet(text=final_tweet)
                parent_tweet_id = response_tweet.data["id"]
                print(f"Xへの本番親投稿が成功しました！ (ID: {parent_tweet_id})")
            except Exception as e:
                print(f"X（Twitter）親投稿エラー: {e}")
                return

            reply_images_groups = [all_media_ids[i:i + 4] for i in range(4, len(all_media_ids), 4)]
            if reply_images_groups:
                reply_target_id = parent_tweet_id
                for g_idx, media_group in enumerate(reply_images_groups):
                    try:
                        reply_text = f"📸 ブログ写真まとめ ({g_idx + 1}/{len(reply_images_groups)})"
                        res_reply = client_x.create_tweet(text=reply_text, media_ids=media_group, in_reply_to_tweet_id=reply_target_id)
                        reply_target_id = res_reply.data["id"]
                        time.sleep(2)
                    except Exception as reply_err:
                        print(f"返信ツリー投稿エラー: {reply_err}")

            for path in temp_files:
                if os.path.exists(path): os.remove(path)
        else:
            print("\n[テストモード] Xへの投稿処理はスキップされました。")
            
        line_message = f"\n【X投稿内容（テストモード）】\n{final_tweet}" if IS_TEST_MODE else f"\n【X投稿内容】\n{final_tweet}"
        send_line_message(line_message, image_urls=all_extracted_image_urls)
        
    else:
        print("対象期間（前日）内に、アンジュルム公式ブログおよび他グループの言及ブログは存在しませんでした。")

if __name__ == "__main__":
    main()
