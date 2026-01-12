import os
import secrets
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# --- KONFIGURATSIYA ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'safechat_ultra_secure_2026_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///safechat_v2.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

# Papkalarni yaratish (rasm, video, fayllar uchun)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'avatars'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'media'), exist_ok=True)

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
CORS(app)

# --- MA'LUMOTLAR BAZASI MODELLARI ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    ip_address = db.Column(db.String(50))
    avatar_url = db.Column(db.String(300), default='')
    is_blocked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(80), nullable=False)
    receiver = db.Column(db.String(80), nullable=False)
    content = db.Column(db.Text, nullable=False)
    msg_type = db.Column(db.String(20), default='text') # text, image, video, file, location
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# --- API ENDPOINTLAR ---

# 1. Login va Registration tizimi
@app.route('/api/login', methods=['POST'])
def auth():
    data = request.json
    email = data.get('email')
    username = data.get('username')
    password = data.get('password')

    user = User.query.filter((User.username == username)).first()

    if not user:
        # Foydalanuvchi yo'q bo'lsa - Ro'yxatdan o'tkazish
        if not email or email == "login@user.com":
            return jsonify({"message": "Ro'yxatdan o'tish uchun email kiriting!"}), 400
        
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(
            email=email,
            username=username,
            password=hashed_pw,
            ip_address=request.remote_addr
        )
        db.session.add(new_user)
        db.session.commit()
        return jsonify({"status": "created", "username": username}), 201

    # Foydalanuvchi bo'lsa - Kirishni tekshirish
    if user.is_blocked:
        return jsonify({"message": "Sizning hisobingiz bloklangan!"}), 403

    if check_password_hash(user.password, password):
        user.ip_address = request.remote_addr
        db.session.commit()
        return jsonify({"status": "success", "username": user.username}), 200
    
    return jsonify({"message": "Parol noto'g'ri!"}), 401

# 2. Profilni yangilash
@app.route('/api/profile/update', methods=['POST'])
def update_profile():
    data = request.json
    user = User.query.filter_by(username=data.get('current_username')).first()
    
    if not user: return jsonify({"message": "Foydalanuvchi topilmadi"}), 404

    if data.get('new_username'):
        new_u = data['new_username'] + ".connect.uz"
        if User.query.filter_by(username=new_u).first():
            return jsonify({"message": "Bu username band!"}), 400
        user.username = new_u

    if data.get('new_password'):
        user.password = generate_password_hash(data['new_password'], method='pbkdf2:sha256')

    db.session.commit()
    return jsonify({"status": "updated"})

# 3. Multimedia yuklash (Foto, Video, Fayl)
@app.route('/api/upload_avatar', methods=['POST'])
def upload_file():
    if 'file' not in request.files: return "Fayl yo'q", 400
    file = request.files['file']
    filename = secure_filename(f"{secrets.token_hex(8)}_{file.filename}")
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], 'media', filename)
    file.save(save_path)
    return jsonify({"url": f"/uploads/media/{filename}"})

# Statik fayllarni ko'rsatish
@app.route('/uploads/<path:type>/<path:filename>')
def serve_files(type, filename):
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], type), filename)

# 4. Admin uchun ma'lumotlar
@app.route('/api/admin/users', methods=['GET'])
def get_users():
    users = User.query.all()
    return jsonify([{
        "username": u.username,
        "ip": u.ip_address,
        "is_blocked": u.is_blocked
    } for u in users])

# --- SOCKET.IO (REAL-TIME CHAT) ---
user_rooms = {}

@socketio.on('join')
def on_join(data):
    username = data.get('username')
    if not username:
        return

    # Foydalanuvchi username → SID xaritasiga yoziladi
    user_rooms[username] = request.sid

    # Socketni foydalanuvchi username xonasiga qo‘shish
    join_room(username)

    print(f"{username} ulandi! SID: {request.sid}")

@socketio.on('send_message')
def handle_msg(data):
    try:
        sender = data.get('sender')
        receiver = data.get('receiver')
        content = data.get('content')
        msg_type = data.get('type', 'text')

        if not sender or not receiver or not content:
            print(f"Xatolik: Ma'lumotlar to'liq emas! {data}")
            return

        # Bazaga saqlash
        new_msg = Message(
            sender=sender,
            receiver=receiver,
            content=content,
            msg_type=msg_type
        )
        db.session.add(new_msg)
        db.session.commit()

        # Server vaqtini qo‘shish
        data['timestamp'] = datetime.utcnow().strftime('%H:%M')

        # Xabarni yuborish
        emit('receive_message', data, room=receiver)  # receiver username room
        emit('receive_message', data, room=sender)    # sender username room

        print(f"Xabar yuborildi: {sender} -> {receiver} [{msg_type}]")

    except Exception as e:
        db.session.rollback()
        print(f"Socket xatoligi: {str(e)}")



if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, host='0.0.0.0', port=port)

