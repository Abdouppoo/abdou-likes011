from flask import Flask, request, jsonify
import json, os, aiohttp, asyncio, requests, binascii, threading, time, concurrent.futures
from datetime import datetime, timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToJson
import like_pb2, like_count_pb2, uid_generator_pb2
from google.protobuf.message import DecodeError
import logging
from queue import Queue
import signal
import sys

app = Flask(__name__)

# تهيئة الملفات
ACCOUNTS_FILE = 'accounts.json'
JWT_FILE = 'jwt.json'
UPDATE_INTERVAL = 4 * 60 * 60  # 4 ساعات بالثواني

# تهيئة التخزين
accounts_jwt = {}
jwt_lock = threading.Lock()
update_thread = None
running = True

# إعداد التسجيل
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ✅ تحميل الحسابات من ملف
def load_accounts():
    try:
        if os.path.exists(ACCOUNTS_FILE):
            with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                accounts = json.load(f)
                logger.info(f"تم تحميل {len(accounts)} حساب من {ACCOUNTS_FILE}")
                return accounts
        else:
            logger.warning(f"ملف {ACCOUNTS_FILE} غير موجود")
            return {}
    except Exception as e:
        logger.error(f"خطأ في تحميل الحسابات: {e}")
        return {}

# ✅ حفظ JWT إلى ملف
def save_jwt_to_file():
    try:
        with jwt_lock:
            with open(JWT_FILE, 'w', encoding='utf-8') as f:
                json.dump(accounts_jwt, f, indent=4, ensure_ascii=False)
        logger.info(f"تم حفظ {len(accounts_jwt)} توكن JWT إلى {JWT_FILE}")
    except Exception as e:
        logger.error(f"خطأ في حفظ JWT: {e}")

# ✅ تحميل JWT من ملف
def load_jwt_from_file():
    try:
        if os.path.exists(JWT_FILE):
            with open(JWT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data
        return {}
    except Exception as e:
        logger.error(f"خطأ في تحميل JWT: {e}")
        return {}

# ✅ جلب التوكن بسرعة باستخدام خيوط متعددة
def fetch_token_thread(uid, password):
    url = f"https://zix-official-jwt.vercel.app/get?uid={uid}&password={password}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0 and "token" in data[0]:
                return uid, data[0]["token"]
            elif isinstance(data, dict) and "token" in data:
                return uid, data["token"]
    except requests.RequestException as e:
        logger.error(f"خطأ في جلب التوكن لـ {uid}: {e}")
    except json.JSONDecodeError:
        logger.error(f"استجابة غير صالحة لـ {uid}")
    return uid, None

# ✅ تحديث جميع التوكنات باستخدام ThreadPoolExecutor
def update_all_tokens():
    global accounts_jwt
    
    accounts = load_accounts()
    if not accounts:
        logger.warning("لا توجد حسابات لتحديثها")
        return
    
    logger.info(f"بدء تحديث {len(accounts)} توكن JWT...")
    
    # استخدام ThreadPoolExecutor لمعالجة متعددة
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        # إرسال جميع المهام
        future_to_uid = {executor.submit(fetch_token_thread, uid, pwd): uid for uid, pwd in accounts.items()}
        
        # جمع النتائج
        successful = 0
        failed = 0
        
        for future in concurrent.futures.as_completed(future_to_uid):
            uid = future_to_uid[future]
            try:
                uid_result, token = future.result()
                if token:
                    with jwt_lock:
                        accounts_jwt[uid] = token
                    successful += 1
                    logger.debug(f"تم تحديث التوكن لـ {uid}")
                else:
                    failed += 1
                    logger.warning(f"فشل تحديث التوكن لـ {uid}")
            except Exception as e:
                failed += 1
                logger.error(f"خطأ في معالجة {uid}: {e}")
    
    # حفظ التوكنات إلى ملف
    save_jwt_to_file()
    
    logger.info(f"اكتمل التحديث: نجح {successful}, فشل {failed}")

# ✅ دورة تحديث التوكنات كل 4 ساعات
def token_updater():
    while running:
        try:
            update_all_tokens()
            logger.info(f"الانتظار {UPDATE_INTERVAL//3600} ساعات للتحديث القادم...")
            
            # الانتظار مع التحقق الدوري من حالة التشغيل
            for _ in range(UPDATE_INTERVAL // 10):
                if not running:
                    break
                time.sleep(10)
        except Exception as e:
            logger.error(f"خطأ في دورة التحديث: {e}")
            time.sleep(60)  # الانتظار دقيقة قبل إعادة المحاولة

# ✅ بدء تحديث التوكنات
def start_token_updater():
    global update_thread, running
    
    # تحميل التوكنات الموجودة
    global accounts_jwt
    accounts_jwt = load_jwt_from_file()
    
    # بدء خيط التحديث
    running = True
    update_thread = threading.Thread(target=token_updater, daemon=True)
    update_thread.start()
    logger.info("تم بدء خيط تحديث التوكنات")

# ✅ إيقاف تحديث التوكنات
def stop_token_updater():
    global running
    running = False
    if update_thread:
        update_thread.join(timeout=5)
    logger.info("تم إيقاف خيط تحديث التوكنات")

# ✅ إرسال لايكات بسرعة باستخدام خيوط متعددة
def send_like_thread(enc_uid, token, result_queue):
    url = "https://clientbp.ggblueshark.com/LikeProfile"
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded",
        'Expect': "100-continue",
        'X-Unity-Version': "2018.4.11f1",
        'X-GA': "v1 1",
        'ReleaseVersion': "OB52"
    }
    
    try:
        response = requests.post(url, data=bytes.fromhex(enc_uid), headers=headers, timeout=10)
        result_queue.put(response.status)
        return response.status
    except requests.RequestException as e:
        logger.error(f"خطأ في إرسال لايك باستخدام توكن: {e}")
        result_queue.put(None)
        return None

# ✅ التشفير
def encrypt_message(plaintext):
    key = b'Yg&tc%DEuh6%Zc^8'
    iv = b'6oyZDr22E3ychjM%'
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return binascii.hexlify(cipher.encrypt(pad(plaintext, AES.block_size))).decode()

def create_uid_proto(uid):
    pb = uid_generator_pb2.uid_generator()
    pb.saturn_ = int(uid)
    pb.garena = 1
    return pb.SerializeToString()

def create_like_proto(uid):
    pb = like_pb2.like()
    pb.uid = int(uid)
    return pb.SerializeToString()

def decode_protobuf(binary):
    try:
        pb = like_count_pb2.Info()
        pb.ParseFromString(binary)
        return pb
    except DecodeError:
        return None

def make_request(enc_uid, token):
    url = "https://clientbp.ggblueshark.com/GetPlayerPersonalShow"
    headers = {
            'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
            'Connection': "Keep-Alive",
            'Accept-Encoding': "gzip",
            'Authorization': f"Bearer {token}",
            'Content-Type': "application/x-www-form-urlencoded",
            'Expect': "100-continue",
            'X-Unity-Version': "2018.4.11f1",
            'X-GA': "v1 1",
            'ReleaseVersion': "OB52"
        }
    try:
        res = requests.post(url, data=bytes.fromhex(enc_uid), headers=headers, verify=False, timeout=10)
        return decode_protobuf(res.content)
    except Exception as e:
        logger.error(f"خطأ في طلب المعلومات: {e}")
        return None

# ✅ نقطة النهاية الرئيسية لإرسال الإعجابات
@app.route('/like', methods=['GET'])
def like_handler():
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "Missing UID"}), 400

    # الحصول على التوكنات
    with jwt_lock:
        tokens = list(accounts_jwt.values())
    
    if not tokens:
        return jsonify({"error": "No valid tokens available"}), 401

    # الحصول على معلومات اللاعب قبل الإعجابات
    enc_uid = encrypt_message(create_uid_proto(uid))
    before = make_request(enc_uid, tokens[0])
    if not before:
        return jsonify({"error": "Failed to retrieve player info"}), 500

    before_data = json.loads(MessageToJson(before))
    likes_before = int(before_data.get("AccountInfo", {}).get("Likes", 0))
    nickname = before_data.get("AccountInfo", {}).get("PlayerNickname", "Unknown")

    # إرسال الإعجابات بسرعة باستخدام خيوط متعددة
    enc_like_uid = encrypt_message(create_like_proto(uid))
    success_count = 0
    result_queue = Queue()
    
    # استخدام ThreadPoolExecutor لإرسال متزامن
    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
        # إرسال جميع طلبات الإعجاب
        futures = [executor.submit(send_like_thread, enc_like_uid, token, result_queue) for token in tokens]
        
        # جمع النتائج
        for future in concurrent.futures.as_completed(futures):
            try:
                status = future.result()
                if status == 200:
                    success_count += 1
            except Exception as e:
                logger.error(f"خطأ في معالجة طلب إعجاب: {e}")

    # الحصول على معلومات اللاعب بعد الإعجابات
    after = make_request(enc_uid, tokens[0])
    likes_after = 0
    if after:
        after_data = json.loads(MessageToJson(after))
        likes_after = int(after_data.get("AccountInfo", {}).get("Likes", 0))

    return jsonify({
        "PlayerNickname": nickname,
        "UID": uid,
        "LikesBefore": likes_before,
        "LikesAfter": likes_after,
        "LikesGivenByAPI": likes_after - likes_before,
        "SuccessfulRequests": success_count,
        "TotalTokensUsed": len(tokens),
        "status": 1 if likes_after > likes_before else 2
    })

# ✅ نقطة النهاية لإعادة تحميل التوكنات
@app.route('/reload_tokens', methods=['GET'])
def reload_tokens():
    update_all_tokens()
    return jsonify({
        "status": "success",
        "message": f"تم تحديث {len(accounts_jwt)} توكن",
        "jwt_count": len(accounts_jwt)
    })

# ✅ نقطة النهاية لعرض حالة التوكنات
@app.route('/tokens_status', methods=['GET'])
def tokens_status():
    with jwt_lock:
        return jsonify({
            "total_tokens": len(accounts_jwt),
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "accounts_file": os.path.exists(ACCOUNTS_FILE),
            "jwt_file": os.path.exists(JWT_FILE)
        })

@app.route('/')
def home():
    with jwt_lock:
        token_count = len(accounts_jwt)
    
    return jsonify({
        "status": "online", 
        "message": "Like API is running ✅",
        "active_tokens": token_count,
        "update_interval_hours": UPDATE_INTERVAL // 3600
    })

# ✅ معالج الإشارات لإيقاف نظيف
def signal_handler(sig, frame):
    logger.info("تلقي إشارة إيقاف...")
    stop_token_updater()
    sys.exit(0)

# ✅ هذا لا يُستخدم في Vercel ولكن نتركه للتشغيل المحلي
if __name__ == '__main__':
    # تسجيل معالجات الإشارات
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # بدء تحديث التوكنات
    start_token_updater()
    
    # تشغيل الخادم
    app.run(host='0.0.0.0', port=5000, debug=False)