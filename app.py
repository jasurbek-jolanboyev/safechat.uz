import eventlet
eventlet.monkey_patch()  # ENG TEPADA VA ALOHIDA QATORDA BO'LISHI SHART!

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
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///safechat_v2.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

# Papkalarni yaratish
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'media'), exist_ok=True)

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
CORS(app)

# --- MA'LUMOTLAR BAZASI ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    ip_address = db.Column(db.String(50))
    is_blocked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(80), nullable=False)
    receiver = db.Column(db.String(80), nullable=False)
    content = db.Column(db.Text, nullable=False)
    msg_type = db.Column(db.String(20), default='text')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# --- API ENDPOINTLAR ---

# 0. Home Route (Render "Live" bo'lishi va 404 xatosi chiqmasligi uchun)
@app.route('/')
def index():
    return "SafeChat Server ishlayapti! API ulanishga tayyor."

# 1. Registration
@app.route('/api/register', methods=['POST'])
def register_api():
    data = request.json
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"message": "Bu username allaqachon band!"}), 400
    
    hashed_pw = generate_password_hash(data['password'])
    new_user = User(
        email=data['email'],
        username=data['username'],
        password=hashed_pw,
        ip_address=request.remote_addr
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"status": "success"}), 201

# 2. Login
@app.route('/api/login', methods=['POST'])
def login_api():
    data = request.json
    user = User.query.filter_by(username=data['username']).first()
    
    if user and check_password_hash(user.password, data['password']):
        if user.is_blocked:
            return jsonify({"message": "Siz bloklangansiz!"}), 403
        user.ip_address = request.remote_addr
        db.session.commit()
        return jsonify({"status": "success", "username": user.username}), 200
    
    return jsonify({"message": "Username yoki parol xato!"}), 401

# 3. Xabarlar tarixi
@app.route('/api/messages', methods=['GET'])
def get_messages():
    u1 = request.args.get('user1')
    u2 = request.args.get('user2')
    msgs = Message.query.filter(
        ((Message.sender == u1) & (Message.receiver == u2)) |
        ((Message.sender == u2) & (Message.receiver == u1))
    ).order_by(Message.timestamp.asc()).all()
    
    return jsonify([{
        "sender": m.sender,
        "receiver": m.receiver,
        "content": m.content,
        "type": m.msg_type,
        "timestamp": m.timestamp.strftime('%H:%M')
    } for m in msgs])

# 4. Foydalanuvchilar ro'yxati
@app.route('/api/admin/users', methods=['GET'])
def admin_users():
    users = User.query.all()
    return jsonify([{
        "username": u.username,
        "ip": u.ip_address,
        "is_blocked": u.is_blocked
    } for u in users])

# 5. Profilni yangilash
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
        user.password = generate_password_hash(data['new_password'])

    db.session.commit()
    return jsonify({"status": "updated"})

# 6. Fayl yuklash
@app.route('/api/upload_avatar', methods=['POST'])
def upload_file_api():
    if 'file' not in request.files: return jsonify({"message": "Fayl topilmadi"}), 400
    file = request.files['file']
    filename = secure_filename(f"{secrets.token_hex(4)}_{file.filename}")
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], 'media', filename)
    file.save(save_path)
    return jsonify({"url": f"{request.host_url.rstrip('/')}/uploads/media/{filename}"})

@app.route('/uploads/<path:type>/<path:filename>')
def serve_files(type, filename):
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], type), filename)

# --- SOCKET.IO ---
@socketio.on('join')
def handle_join(data):
    username = data.get('username')
    if username:
        join_room(username)

@socketio.on('send_message')
def handle_send(data):
    try:
        new_msg = Message(
            sender=data['sender'],
            receiver=data['receiver'],
            content=data['content'],
            msg_type=data.get('type', 'text')
        )
        db.session.add(new_msg)
        db.session.commit()
        data['timestamp'] = datetime.utcnow().strftime('%H:%M')
        emit('receive_message', data, room=data['receiver'])
        emit('receive_message', data, room=data['sender'])
    except Exception as e:
        db.session.rollback()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, host='0.0.0.0', port=port)
