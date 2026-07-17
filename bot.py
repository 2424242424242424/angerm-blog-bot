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
IS_TEST_MODE = False

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
        print(f"\n【DEBUG】[LINE画像送信セクション] 処理を開始します。対象URL数: {len(image_urls)}")
        for idx in range(0, len(image_urls), 4):
            chunk = image_urls[idx:idx+4]
            messages = []
            for img_url in chunk:
                # 【最重要】LINE Messaging APIの仕様厳守のため、送信時のみ強制的・確実に https:// へ変換
                secure_url = img_url.replace("http://", "https://")
                print(f"【DEBUG】LINE送信確定URL(HTTPS化): {secure_url}")
                messages.append({
                    "type": "image",
                    "originalContentUrl": secure_url,
                    "previewImageUrl": secure_url
                })
            payload_image = {
                "to": user_id,
                "messages": messages
            }
            try:
                data_image = json.dumps(payload_image).encode("utf-8")
                req = urllib.request.Request(url, data=data_image, headers=headers, method="POST")
                with urllib.request.urlopen(req) as res:
                    print(f"【DEBUG】LINE画像送信API レスポンスステータス: {res.getcode()}")
                    if res.getcode() == 200:
                        print(f"LINEへの画像通知 ({idx+1}〜{idx+len(chunk)}枚目) が成功しました！")
            except Exception as e:
                print(f"【DEBUG】LINE画像通知API演行エラー: {e}")
    else:
        print("\n【DEBUG】[LINE画像送信セクション] image_urls が空またはNoneのためスキップされました。")

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

    angerme_keywords = (
        r"アンジュルム|アンジュ|スマイレージ|スマ|"
        r"上國料|かみこ|萌衣|川村|文乃|かわむー|かむ|伊勢|鈴蘭|れいら|れら|れらたん|橋迫|鈴|鈴ちゃん|"
        r"川名|凜|ケロ|ケロちゃん|為永|幸音|しおんぬ|ため|松本|わかな|わかにゃ|平山|遊季|ゆき|ぺいぺい|ぺい|"
        r"下井谷|幸穂|ゆっぴょん|ゆきほ|後藤|花|はなな|ごっちん|長野|桃羽|もっち|もち|ももは"
    )
    
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    
    yesterday = now - timedelta(days=1)
    start_of_yesterday = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_yesterday = yesterday.replace(hour=23, minute=59, second=59, microsecond=0)
    
    print(f"現在時刻: {now.strftime('%Y-%m-%d %H:%M:%S')} (JST)")
    print(f"対象期間（前日限定）: {start_of_yesterday.strftime('%Y-%m-%d %H:%M:%S')} 〜 {end_of_yesterday.strftime('%Y-%m-%d %H:%M:%S')}\n")

    client_gemini = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    processed_tweets_data = []      
    mention_tweets_data = []        
    all_extracted_image_urls = []   

    # ==========================================
    # 処理①：各グループのブログRSSを巡回・スキャン
    # ==========================================
    for group_key, rss_url in rss_urls.items():
        print(f"【RSSスキャン中】: {group_key}")
        try:
            req_rss = urllib.request.Request(rss_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_rss) as response:
                xml_raw_bytes = response.read()
            root = ET.fromstring(xml_raw_bytes)
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
                
                # 記事HTMLスクレイピング（検証済み・完璧に機能中）
                if link_url:
                    print(f"   【DEBUG】記事HTMLから直接画像URLを取得します: {link_url}")
                    try:
                        req_html = urllib.request.Request(link_url, headers={'User-Agent': 'Mozilla/5.0'})
                        with urllib.request.urlopen(req_html) as html_res:
                            html_content = html_res.read().decode('utf-8', errors='ignore')
                        
                        raw_img_matches = re.findall(r'https://stat\.ameba\.jp/user_images/[^\s"\'<>&\?]+', html_content)
                        print(f"   【DEBUG】HTML内から見つかった候補数: {len(raw_img_matches)}")
                        
                        for url in raw_img_matches:
                            url = url.split('"')[0].split("'")[0].split('>')[0].split('<')[0].split(' ')[0]
                            if "charimages" in url or "blog_import" in url:
                                continue
                            if not any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png"]):
                                continue
                            
                            # Xダウンロード用に一度 http:// で保持
                            http_url = url.replace("https://", "http://")
                            if http_url not in corrected_img_urls:
                                print(f"   【DEBUG】画像URL取得成功: {http_url}")
                                corrected_img_urls.append(http_url)
                    except Exception as html_err:
                        print(f"   【DEBUG】記事HTMLの直接取得失敗: {html_err}")

                if not corrected_img_urls and description:
                    raw_img_matches = re.findall(r'https://stat\.ameba\.jp/user_images/[^\s"\'<>&\?]+', description)
                    for url in raw_img_matches:
                        url = url.split('"')[0].split("'")[0].split('>')[0].split('<')[0].split(' ')[0]
                        if "charimages" in url or "blog_import" in url or not any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png"]):
                            continue
                        http_url = url.replace("https://", "http://")
                        if http_url not in corrected_img_urls:
                            corrected_img_urls.append(http_url)
                
                for url in corrected_img_urls:
                    if url not in all_extracted_image_urls:
                        all_extracted_image_urls.append(url)

                prompt_text = (
                    f"あなたはアンジュルムを温かく見守る広報アシスタントです。\n"
                    f"メンバーのブログから『日々の楽しさや感情が伝わる部分』を抽出し、指定のフォーマットで【超要約】を1つ作成してください。\n\n"
                    f"■ メンバー名(テーマ): {theme}\n"
                    f"■ 今回のブログタイトル: {title}\n"
                    f"■ 今回の本文: {description[:500]}...\n\n"
                    f"【出力フォーマットと表現の厳格なルール】\n"
                    f"1. 挨拶、タイトル等は一切出力せず、純粋な要約文（2〜3行程度）だけを出力してください。\n"
                    f"2. ブログにある日常の出来事や感想などの内容を拾って構成してください。\n"
                    f"3. 【最重要】ブログの最後によくある「告知情報・お知らせ」は絶対に含めず、完全に無視してください。\n"
                    f"4. 文章のなかに必ず指定のあだ名（れら、鈴ちゃん、しおんぬ、ケロ、わかにゃ、ゆきちゃん、ゆっぴょん、はなな、もち）を用いて主語を明確にしてください。\n"
                    f"5. メンバーの口調のまま表現する部分は「」書きに、客観的なまとめは「」なしにしてください。\n"
                    f"6. 【厳守】全体の文字数は、必ず70文字以内（厳守）にしてください。\n"
                    f"7. ブログ内の具体的な場所を特定・推測できる情報は絶対に記載禁止です。\n"
                    f"8. 文頭に、メンバーを表す絵文字を入れてください（れら→🦐、鈴ちゃん→🔔、しおんぬ→🎶、ケロ→🐸、ゆきちゃん→❄、わかにゃ→🍞、ゆっぴょん→🐰、はなな→🌼、もち→🎨）。\n"
                    f"9. 【難しいニュアンスは要約をスキップしてOK】\n"
                    f"   ブログ内の複雑な言い回しや哲学的な考察は、文字数制限の中で誤読を避けるため無理に要約しなくてOKです。メンバーの「嬉しい！」「美味しい！」といった、素直で分かりやすい感情の動きや日常のワンシーンを優先して拾ってください。\n"
                    f"10. 【短くテンポ良く、人情味のある温かいトーン】\n"
                    f"   「〜と述べる」「〜と綴っている」「〜とのこと。」のような事務的で冷たい表現は禁止です。\n"
                    f"   短い言葉でテンポよくまとめ、文末に「！」や1人1個まで絵文字を使ったり、「〜して嬉しい様子」「〜できたみたい」など、ファンとしての温かみや人情味を感じる親しみやすい言葉遣いで要約してください。\n"
                    f"   行間や文脈を深く分析し、メンバーの若者としての新鮮な感性を肯定する視点で要約してください。"                    
                )

                contents = [prompt_text]
                if corrected_img_urls:
                    try:
                        req_img = urllib.request.Request(corrected_img_urls[0], headers={'User-Agent': 'Mozilla/5.0'})
                        img_data = urllib.request.urlopen(req_img).read()
                        contents.append(types.Part.from_bytes(data=img_data, mime_type="image/jpeg"))
                        print("   【DEBUG】Gemini送信用の第1画像オブジェクト化に成功")
                    except Exception as e:
                        print(f"   [Gemini用画像読み込み失敗] {e}")

                try:
                    response = client_gemini.models.generate_content(model='gemini-2.5-flash', contents=contents)
                    result_text = response.text.strip() if response.text else ""
                    if result_text: processed_tweets_data.append(result_text)
                except Exception as e:
                    print(f"   Gemini APIエラー: {e}")

            # --- 他のハロプロブログの場合の処理（アンジュルム言及チェック） ---
            else:
                if re.search(angerme_keywords, title) or (description and re.search(angerme_keywords, description)):
                    prompt_mention = (
                        f"あなたはハロー！プロジェクトの熱心なファンであり、厳密なフィルター検閲を行う優秀な広報アシスタントです。\n"
                        f"提供されたブログの文章を【超厳密に】解析し、指定された【本物のアンジュルム現役メンバー】または【アンジュルムというグループ全体】に対する具体的な言及や直接の交流（ツーショット、会話、具体的なエピソードなど）が【本文中に確実に存在する場合のみ】、指定のフォーマットで要約を作成してください。\n\n"
                        f"【⚠️最重要：アンジュルム現役メンバーとあだ名の対応表】\n"
                        f"・長野桃羽 (あだ名: もっち、もち、ももは) ※「もっち」は平山遊季や松本わかなのことではありません\n"
                        f"・上國料萌衣 (かみこ、かみ) / 川村文乃 (かわむー、かむ) / 伊勢鈴蘭 (れら、れらたん、れらぴ) / 橋迫鈴 (鈴ちゃん、りんこ、はっさこ)\n"
                        f"・川名凜 (ケロ、ケロちゃん、ケロンヌ、なりん、ンヌ) / 為永幸音 (しおんぬ、ため、んぬ、ためんぬ) / 松本わかな (わかにゃ、わーちゃん)\n"
                        f"・平山遊季 (ゆきちゃん、ぺい、ぺいぺい) / 下井谷幸穂 (ゆっぴょん、ゆぴょ、もい、もいもい) / 後藤花 (はなな、ごっちん、)\n\n"
                        f"【❌ 厳格な誤判定・ハルシネーション防止ルール（厳守）】\n"
                        f"1. **「言及なし」なら絶対に空白で返すこと**：\n"
                        f"   提供された本文の中に、上記のアンジュルムメンバー（あだ名含む）や「アンジュルム」というグループ名への具体的な言及が【1箇所も存在しない場合】は、絶対に要約を作成しないでください。\n"
                        f"   その場合は、言い訳や「該当なし」などの説明文も一切出力せず、完全に【空白（空文字）】のみを返してください。\n"
                        f"2. **無関係な単語での誤検知禁止**：\n"
                        f"   他グループのメンバー（例：櫻井梨央の「らいりー」「りーちゃん」等）や、一般名詞の「雪（ゆき）」「花（はな）」、または「あんじゅ」といった名前の一般人など、アンジュルム現役メンバーとは無関係な単語に引っかかって要約を作らないでください。文脈を正しく読み、関係がなければ【空白（空文字）】で返してください。\n"
                        f"3. **投稿者名の勝手な変更禁止**：\n"
                        f"   ブログの投稿者は必ず「テーマ: {theme}」の人物です。勝手に投稿者名を変更したり捏造したりしないでください。\n\n"
                        f"■ 投稿者グループ: {group_key}\n"
                        f"■ 投稿者名(テーマ): {theme}\n"
                        f"■ ブログタイトル: {title}\n"
                        f"■ 本文: {description[:450] if description else ''}...\n\n"
                        f"【出力フォーマット・トンマナの厳格なルール】※該当する場合のみ\n"
                        f"1. 挨拶や前置きは一切出力せず、純粋な要約文（URL除いて70文字以内）だけを出力してください。\n"
                        f"2. アンジュルムのメンバーの記述には、必ず上記の【あだ名】（例: もっち、かみこ、わかにゃ等）を使用してください。\n"
                        f"3. メンバーの口調のまま表現する部分は「」書きに、客観的なまとめは「」なしに構成してください。\n"
                        f"4. 文章の最後に、ブログのURL（ {link_url} ）を必ず添えてください。\n"
                        f"5. 文頭のグループ名略称のブラケット部分は、提供された【テーマ名: {theme}】をそのまま使い、以下の指定6パターン＋OGのいずれかの形式で出力してください。\n"
                        f"   ・[娘。{theme}] / [つばき{theme}] / [Juice{theme}] / [OCHA{theme}] / [BEYO{theme}] / [ロージー{theme}] / [OG{theme}]\n"
                        f"   (例: 💬 [娘。{theme}]、💬 [Juice{theme}] )"
                    )



                    try:
                        response = client_gemini.models.generate_content(model='gemini-2.5-flash', contents=[prompt_mention])
                        result_text = response.text.strip() if response.text else ""
                    except Exception as e:
                        print(f"   Gemini APIエラー(テキスト判定): {e}")
                        result_text = ""

                    if result_text and not any(msg in result_text for msg in ["言及はありません", "表示不要", "対象外", "見つかりませんでした"]):
                        print(f" -> 【他グループ言及確定】[{group_key}] {theme} - {title}")
                        mention_tweets_data.append(result_text)

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
            f"​※AI要約のため、事実や本人の意図と異なる内容となる場合がございます。\n"
            f"🔗 一覧: https://ameblo.jp/angerme-new/"
        )
        
        if mention_tweets_data:
            mention_text = "\n\n".join(mention_tweets_data)
            final_tweet += f"\nーーー\n✉️他のハロメンやOGより\n\n{mention_text}"
        
        print("\n[投稿内容の確認]")
        print(final_tweet)
        
        print(f"\n【DEBUG】[投稿一括処理セクション] 最終的に集まった全画像URL総数: {len(all_extracted_image_urls)}")

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
