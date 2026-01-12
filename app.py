import os
import secrets
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'safechat_ultra_secure_2026_key'
# Renderda DATABASE_URL bo'lsa shuni oladi, bo'lmasa sqlite ishlatadi
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///safechat_v2.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

# Papkalar yaratish
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'media'), exist_ok=True)

db = SQLAlchemy(app)
# Renderda WebSocket barqaror ishlashi uchun eventlet va polling sozlamalari
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
CORS(app)

# --- MODELLAR ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    ip_address = db.Column(db.String(50))
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

# --- API ---

@app.route('/api/login', methods=['POST'])
def auth():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    email = data.get('email') # Registratsiya uchun kerak

    user = User.query.filter_by(username=username).first()

    if not user:
        # Registratsiya qismi
        if not email:
            return jsonify({"message": "Ro'yxatdan o'tish uchun email kerak"}), 400
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(email=email, username=username, password=hashed_pw, ip_address=request.remote_addr)
        db.session.add(new_user)
        db.session.commit()
        return jsonify({"status": "created", "username": username}), 201

    if check_password_hash(user.password, password):
        return jsonify({"status": "success", "username": user.username}), 200
    
    return jsonify({"message": "Parol noto'g'ri!"}), 401

# Yangi endpoint: Xabarlar tarixini olish
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

# Foydalanuvchilarni qidirish (Xavfsiz variant)
@app.route('/api/admin/users', methods=['GET'])
def list_users():
    users = User.query.all()
    return jsonify([{"username": u.username} for u in users])

@app.route('/api/upload_avatar', methods=['POST'])
def upload_file():
    if 'file' not in request.files: return "Fayl yo'q", 400
    file = request.files['file']
    filename = secure_filename(f"{secrets.token_hex(8)}_{file.filename}")
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], 'media', filename)
    file.save(save_path)
    return jsonify({"url": f"{request.host_url}uploads/media/{filename}"})

@app.route('/uploads/<path:type>/<path:filename>')
def serve_files(type, filename):
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], type), filename)

# --- SOCKET ---

@socketio.on('join')
def on_join(data):
    username = data.get('username')
    if username:
        join_room(username)
        print(f"DEBUG: {username} xonaga kirdi")

@socketio.on('send_message')
def handle_msg(data):
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
        print(f"ERROR: {e}")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, host='0.0.0.0', port=port)
