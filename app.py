import eventlet
eventlet.monkey_patch()  # Socket.io uchun eng tepada bo'lishi shart

import os
import secrets
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# --- KONFIGURATSIYA ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'safechat_ultra_secure_2026_key'
# Render.com yoki mahalliy SQLite bazasi
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///safechat_v3.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

# Papkalarni yaratish (Media va fayllar uchun)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'media'), exist_ok=True)

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
CORS(app, resources={r"/api/*": {"origins": "*"}})

# --- MA'LUMOTLAR BAZASI MODELLARI ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=True)
    avatar = db.Column(db.Text, nullable=True)
    is_blocked = db.Column(db.Boolean, default=False)
    is_online = db.Column(db.Boolean, default=False)
    blocked_users = db.Column(db.Text, default="") # SHU QATORNI QO'SHING

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(100), nullable=False)
    receiver = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    msg_type = db.Column(db.String(20), default='text')
    # Reply (javob) ma'lumotlarini JSON formatida saqlash uchun ustun
    reply_info = db.Column(db.Text, nullable=True) 
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Entity(db.Model): 
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    creator = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(20)) # 'group' yoki 'channel'
    members = db.Column(db.Text) 

# Bazani yaratish
with app.app_context():
    db.create_all()

# --- YORDAMCHI FUNKSIYALAR ---

def user_is_blocked_by(target_username, sender_username):
    """Target foydalanuvchi senderni bloklaganmi?"""
    user = User.query.filter_by(username=target_username).first()
    if user and user.blocked_users:
        return sender_username in user.blocked_users.split(',')
    return False

# --- API ENDPOINTLAR ---

# ... mavjud importlar ...

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory(os.getcwd(), 'manifest.json')

@app.route('/logo.png')
def serve_logo():
    return send_from_directory(os.getcwd(), 'logo.png')

# Agarda sw.js (Service Worker) ham yaratgan bo'lsangiz:
@app.route('/sw.js')
def serve_sw():
    return send_from_directory(os.getcwd(), 'sw.js')

# ... qolgan barcha endpointlar (register, login, etc.) ...

@app.route('/api/recent_chats', methods=['GET'])
def get_recent_chats():
    username = request.args.get('username')
    if not username:
        return jsonify([]), 400

    # Foydalanuvchi ishtirok etgan barcha xabarlarni vaqt bo'yicha teskari tartibda olish
    msgs = Message.query.filter(
        (Message.sender == username) | (Message.receiver == username)
    ).order_by(Message.timestamp.desc()).all()
    
    contacts = []
    seen = set()
    
    for m in msgs:
        # Suhbatdosh kimligini aniqlaymiz
        other_user = m.sender if m.sender != username else m.receiver
        
        if other_user not in seen:
            contacts.append(other_user)
            seen.add(other_user)
    
    return jsonify(contacts)

@app.route('/')
def index():
    return "SafeChat V3 Server is Running!"

@app.route('/api/register', methods=['POST'])
def register_api():
    data = request.json
    # Tekshiruv: Username yoki Telefon bandmi?
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"message": "Bu username band!"}), 400
    if User.query.filter_by(phone=data['phone']).first():
        return jsonify({"message": "Bu telefon raqami ro'yxatdan o'tgan!"}), 400

    hashed_p = generate_password_hash(data['password'])
    new_user = User(
        username=data['username'],
        password=hashed_p,
        phone=data['phone']
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"status": "success", "message": "Ro'yxatdan o'tdingiz!"}), 201

@app.route('/api/login', methods=['POST'])
def login_api():
    try:
        data = request.json
        # Username ni tekshirish
        user = User.query.filter_by(username=data['username']).first()
        
        if user and check_password_hash(user.password, data['password']):
            if user.is_blocked:
                return jsonify({"message": "Profilingiz bloklangan! CEO bilan bog'laning."}), 403
            
            # Login muvaffaqiyatli bo'lsa
            user.is_online = True
            db.session.commit()
            
            return jsonify({
                "status": "success", 
                "username": user.username,
                "avatar": user.avatar or f"https://ui-avatars.com/api/?name={user.username}"
            }), 200
            
        return jsonify({"message": "Username yoki parol xato!"}), 401
    except Exception as e:
        return jsonify({"message": f"Server xatosi: {str(e)}"}), 500

import json # Fayl tepasida borligiga ishonch hosil qiling

@app.route('/api/admin/users', methods=['GET'])
def admin_get_users():
    # Bu yerda admin ekanligini tekshirish (auth) qo'shish tavsiya etiladi
    users = User.query.all()
    output = []
    for user in users:
        output.append({
            "name": user.username,
            "status": "ONLINE" if user.is_online else "OFFLINE",
            "is_blocked": user.is_blocked,
            "phone": user.phone
        })
    return jsonify(output)

@socketio.on('disconnect')
def handle_disconnect():
    # Bu yerda foydalanuvchini aniqlab is_online = False qilish mumkin
    print("Foydalanuvchi tarmoqdan uzildi")

# Foydalanuvchini bloklash uchun
@app.route('/api/admin/block', methods=['POST'])
def admin_block_user():
    data = request.json
    user = User.query.filter_by(username=data['username']).first()
    if user:
        user.is_blocked = not user.is_blocked # Bloklash yoki blokdan ochish
        db.session.commit()
        return jsonify({"message": "Muvaffaqiyatli!"}), 200
    return jsonify({"message": "User topilmadi"}), 404

@socketio.on('admin_action')
def handle_admin_action(data):
    # data: { action: 'ban', target: 'user1' }
    action = data.get('action')
    target = data.get('target')
    
    if action == 'ban':
        # Bazada userni blocklash kodi
        emit('user_banned', {'target': target}, broadcast=True)

@app.route('/api/messages', methods=['GET'])
def get_messages():
    u1 = request.args.get('user1')
    u2 = request.args.get('user2')
    
    if not u1 or not u2:
        return jsonify({"message": "Foydalanuvchilar ko'rsatilmadi"}), 400

    try:
        # u2 guruh yoki foydalanuvchi ekanini aniqlash
        is_entity = Entity.query.filter_by(name=u2).first()
        
        if is_entity:
            # Guruh xabarlari
            msgs = Message.query.filter_by(receiver=u2).order_by(Message.timestamp.asc()).all()
        else:
            # Shaxsiy xabarlar
            msgs = Message.query.filter(
                ((Message.sender == u1) & (Message.receiver == u2)) |
                ((Message.sender == u2) & (Message.receiver == u1))
            ).order_by(Message.timestamp.asc()).all()
        
        # JSON javobni shakllantirish
        result = []
        for m in msgs:
            # MUHIM: reply_info matnini qaytadan JSON (obyekt)ga aylantiramiz
            reply_to_obj = None
            if hasattr(m, 'reply_info') and m.reply_info:
                try:
                    reply_to_obj = json.loads(m.reply_info)
                except:
                    reply_to_obj = None

            result.append({
                "id": m.id,
                "sender": m.sender,
                "receiver": m.receiver,
                "content": m.content,
                "type": m.msg_type,
                "is_edited": getattr(m, 'is_edited', False), # Agar ustun bo'lsa oladi
                "timestamp": m.timestamp.strftime('%H:%M'),
                "reply_to": reply_to_obj # Front-endga obyekt sifatida ketadi
            })
        
        return jsonify(result)

    except Exception as e:
        print(f"Xabarlarni yuklashda xato: {e}")
        return jsonify({"message": "Xabarlarni yuklab bo'lmadi"}), 500

@app.route('/api/users/search', methods=['GET'])
def search_users():
    # Faqat username va statusni qaytaramiz (Xavfsiz qidiruv)
    users = User.query.all()
    return jsonify([{"username": u.username, "is_blocked": u.is_blocked} for u in users])

@app.route('/api/upload_avatar', methods=['POST'])
def upload_file_api():
    if 'file' not in request.files: return jsonify({"message": "Fayl yo'q"}), 400
    file = request.files['file']
    ext = file.filename.split('.')[-1]
    filename = secure_filename(f"{secrets.token_hex(8)}.{ext}")
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], 'media', filename)
    file.save(save_path)
    # Front-end API manzili bilan qo'shib olishi uchun URL qaytaramiz
    return jsonify({"url": f"/uploads/media/{filename}"})

@app.route('/uploads/<path:type>/<path:filename>')
def serve_files(type, filename):
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], type), filename)

@app.route('/api/entities', methods=['GET'])
def get_entities():
    username = request.args.get('username')
    # Foydalanuvchi a'zo bo'lgan barcha guruh va kanallarni topish
    entities = Entity.query.filter(Entity.members.contains(username)).all()
    
    result = []
    for e in entities:
        result.append({
            "name": e.name,
            "type": e.entity_type,
            "creator": e.creator
        })
    return jsonify(result)
# --- SOCKET.IO REAL-TIME (YAKUNIY TO'LIQ VARIANT) ---

@socketio.on('join')
def handle_join(data):
    username = data.get('username')
    if username:
        join_room(username)
        # Foydalanuvchi a'zo bo'lgan guruhlarni topish va ularga ulanish
        entities = Entity.query.filter(Entity.members.contains(username)).all()
        for ent in entities:
            join_room(ent.name)
        print(f"DEBUG: {username} hamma xonalarga ulandi.")

@socketio.on('send_message')
def handle_send(data):
    try:
        sender_u = data.get('sender')
        receiver_u = data.get('receiver')
        msg_type = data.get('type', 'text') # text, image, video, file, location
        content = data.get('content', '')
        
        # 1. Bloklash tekshiruvi (faqat shaxsiy chat bo'lsa)
        is_entity = Entity.query.filter_by(name=receiver_u).first()
        if not is_entity:
            if user_is_blocked_by(receiver_u, sender_u):
                print(f"BLOCKED: {sender_u} -> {receiver_u}")
                return 

        # 2. Bazaga saqlash
        reply_json = json.dumps(data.get('reply_to')) if data.get('reply_to') else None
        
        new_msg = Message(
            sender=sender_u,
            receiver=receiver_u,
            content=content, # Bu yerda matn yoki fayl nomi bo'ladi
            msg_type=msg_type,
            reply_info=reply_json
        )
        db.session.add(new_msg)
        db.session.commit()
        
        # 3. Front-end uchun ma'lumotlarni to'ldirish
        data['id'] = new_msg.id
        data['timestamp'] = datetime.utcnow().strftime('%H:%M')
        
        # Multimedia (Base64) yoki Location ma'lumotlari data ichida o'zi bilan ketadi
        # Front-end ularni 'file_data' yoki 'location_data' sifatida qabul qiladi

        # 4. Xabarni tarqatish
        emit('receive_message', data, to=receiver_u)
        if receiver_u != sender_u:
            emit('receive_message', data, to=sender_u)
            
    except Exception as e:
        print(f"ERROR_SEND: {e}")
        db.session.rollback()

@socketio.on('edit_message')
def handle_edit(data):
    try:
        msg = Message.query.get(data['id'])
        if msg and msg.sender == data.get('sender'):
            msg.content = data['content']
            db.session.commit()
            emit('message_edited', data, to=msg.receiver)
            emit('message_edited', data, to=msg.sender)
    except Exception as e:
        print(f"EDIT_ERROR: {e}")

@socketio.on('delete_message')
def handle_delete(data):
    try:
        msg = Message.query.get(data['id'])
        if msg:
            r, s = msg.receiver, msg.sender
            db.session.delete(msg)
            db.session.commit()
            emit('message_deleted', data['id'], to=r)
            emit('message_deleted', data['id'], to=s)
    except Exception as e:
        print(f"DELETE_ERROR: {e}")

@socketio.on('create_entity')
def handle_create_entity(data):
    name = data.get('name')
    entity_type = data.get('type') # 'group' yoki 'channel'
    creator = data.get('creator')

    if Entity.query.filter_by(name=name).first():
        emit('entity_error', {"message": "Bu nom band!"}, to=creator)
        return

    new_entity = Entity(
        name=name,
        creator=creator,
        entity_type=entity_type,
        members=creator 
    )
    db.session.add(new_entity)
    db.session.commit()
    join_room(name)
    emit('entity_created', data, broadcast=True)

@socketio.on('add_member')
def handle_add_member(data):
    group = Entity.query.filter_by(name=data['group']).first()
    if group:
        members = group.members.split(',') if group.members else []
        if data['username'] not in members:
            members.append(data['username'])
            group.members = ",".join(members)
            db.session.commit()
            join_room(data['group'])
            emit('member_added', data, to=data['group'])

@socketio.on('call_signal')
def handle_call(data):
    # WebRTC signalizatsiyasi uchun (Video/Audio qo'ng'iroq)
    emit('incoming_call', data, to=data['to'])

if __name__ == '__main__':
    # Render.com portini avtomatik aniqlash
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, host='0.0.0.0', port=port)