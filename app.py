import eventlet
eventlet.monkey_patch()  # ENG TEPADA BO'LISHI SHART!

import os
import secrets
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
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///safechat_v3.db')
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
    blocked_users = db.Column(db.Text, default="") # Vergul bilan ajratilgan username'lar
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(80), nullable=False)
    receiver = db.Column(db.String(80), nullable=False) # Username yoki GroupName
    content = db.Column(db.Text, nullable=False)
    msg_type = db.Column(db.String(20), default='text') # text, image, file, location
    is_edited = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Entity(db.Model): # Guruhlar va Kanallar uchun
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    creator = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(20)) # 'group' yoki 'channel'
    members = db.Column(db.Text) # Vergul bilan ajratilgan username'lar

with app.app_context():
    db.create_all()

# --- API ENDPOINTLAR ---

@app.route('/')
def index():
    return "SafeChat V3 Server ishlayapti!"

@app.route('/api/register', methods=['POST'])
def register_api():
    data = request.json
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"message": "Bu username allaqachon band!"}), 400
    
    new_user = User(
        email=data['email'],
        username=data['username'],
        password=generate_password_hash(data['password']),
        ip_address=request.remote_addr
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"status": "success"}), 201

@app.route('/api/login', methods=['POST'])
def login_api():
    data = request.json
    user = User.query.filter_by(username=data['username']).first()
    if user and check_password_hash(user.password, data['password']):
        if user.is_blocked: return jsonify({"message": "Siz bloklangansiz!"}), 403
        return jsonify({"status": "success", "username": user.username}), 200
    return jsonify({"message": "Xato!"}), 401

@app.route('/api/messages', methods=['GET'])
def get_messages():
    u1 = request.args.get('user1')
    u2 = request.args.get('user2')
    # Shaxsiy yoki Guruh xabarlarini olish
    msgs = Message.query.filter(
        ((Message.sender == u1) & (Message.receiver == u2)) |
        ((Message.sender == u2) & (Message.receiver == u1)) |
        (Message.receiver == u2) # Guruh uchun u2 bu guruh nomi
    ).order_by(Message.timestamp.asc()).all()
    
    return jsonify([{
        "id": m.id,
        "sender": m.sender,
        "receiver": m.receiver,
        "content": m.content,
        "type": m.msg_type,
        "is_edited": m.is_edited,
        "timestamp": m.timestamp.strftime('%H:%M')
    } for m in msgs])

@app.route('/api/admin/users', methods=['GET'])
def admin_users():
    users = User.query.all()
    return jsonify([{"username": u.username, "ip": u.ip_address, "is_blocked": u.is_blocked} for u in users])

@app.route('/api/upload_avatar', methods=['POST'])
def upload_file_api():
    if 'file' not in request.files: return jsonify({"message": "Xato"}), 400
    file = request.files['file']
    filename = secure_filename(f"{secrets.token_hex(4)}_{file.filename}")
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], 'media', filename)
    file.save(save_path)
    return jsonify({"url": f"{request.host_url.rstrip('/')}/uploads/media/{filename}"})

@app.route('/uploads/<path:type>/<path:filename>')
def serve_files(type, filename):
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], type), filename)

# --- SOCKET.IO (ASOSIY FUNKSIYALAR) ---

@socketio.on('join')
def handle_join(data):
    username = data.get('username')
    join_room(username)
    # Foydalanuvchi a'zo bo'lgan barcha guruhlarga ham join qilish
    entities = Entity.query.filter(Entity.members.contains(username)).all()
    for ent in entities:
        join_room(ent.name)

@socketio.on('send_message')
def handle_send(data):
    # Bloklash tekshiruvi
    target_user = User.query.filter_by(username=data['receiver']).first()
    if target_user and data['sender'] in (target_user.blocked_users or "").split(','):
        return # Xabar yuborilmaydi

    new_msg = Message(
        sender=data['sender'],
        receiver=data['receiver'],
        content=data['content'],
        msg_type=data.get('type', 'text')
    )
    db.session.add(new_msg)
    db.session.commit()
    
    data['id'] = new_msg.id
    data['timestamp'] = datetime.utcnow().strftime('%H:%M')
    
    # Guruhga yoki shaxsga yuborish
    emit('receive_message', data, room=data['receiver'])
    if data['receiver'] != data['sender']:
        emit('receive_message', data, room=data['sender'])

@socketio.on('edit_message')
def handle_edit(data):
    msg = Message.query.get(data['id'])
    if msg and msg.sender == data.get('sender', msg.sender):
        msg.content = data['content']
        msg.is_edited = True
        db.session.commit()
        emit('message_edited', {"id": data['id'], "content": data['content']}, room=data['receiver'])
        emit('message_edited', {"id": data['id'], "content": data['content']}, room=msg.sender)

@socketio.on('delete_message')
def handle_delete(data):
    msg = Message.query.get(data['id'])
    if msg:
        receiver = msg.receiver
        sender = msg.sender
        db.session.delete(msg)
        db.session.commit()
        emit('message_deleted', data['id'], room=receiver)
        emit('message_deleted', data['id'], room=sender)

@socketio.on('create_entity')
def handle_create_entity(data):
    # Guruh yoki kanal yaratish
    new_ent = Entity(
        name=data['name'],
        creator=data['creator'],
        entity_type=data['type'],
        members=data['creator']
    )
    db.session.add(new_ent)
    db.session.commit()
    join_room(data['name'])
    emit('entity_created', data, room=data['creator'])

@socketio.on('block_user')
def handle_block(data):
    # data: { sender: "men", target: "u" }
    user = User.query.filter_by(username=data['sender']).first()
    if user:
        current_blocks = user.blocked_users.split(',') if user.blocked_users else []
        if data['target'] not in current_blocks:
            current_blocks.append(data['target'])
            user.blocked_users = ",".join(current_blocks)
            db.session.commit()
            emit('user_blocked_status', {"target": data['target'], "status": "blocked"}, room=data['sender'])

@socketio.on('call_signal')
def handle_call(data):
    # data: { to: "username", from: "username", type: "video/audio" }
    emit('incoming_call', data, room=data['to'])

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, host='0.0.0.0', port=port)
