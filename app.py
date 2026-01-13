import eventlet
eventlet.monkey_patch()  # Socket.io uchun eng tepada bo'lishi shart

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
# Render.com yoki mahalliy SQLite bazasi
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///safechat_v3.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

# Papkalarni yaratish (Media va fayllar uchun)
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
    is_blocked = db.Column(db.Boolean, default=False)
    blocked_users = db.Column(db.Text, default="")  # Vergul bilan ajratilgan username'lar
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(80), nullable=False)
    receiver = db.Column(db.String(80), nullable=False) 
    content = db.Column(db.Text, nullable=False)
    msg_type = db.Column(db.String(20), default='text') 
    is_edited = db.Column(db.Boolean, default=False)
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

@app.route('/')
def index():
    return "SafeChat V3 Server is Running!"

@app.route('/api/register', methods=['POST'])
def register_api():
    data = request.json
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"message": "Bu username band!"}), 400
    
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
        if user.is_blocked: return jsonify({"message": "Profilingiz bloklangan!"}), 403
        return jsonify({"status": "success", "username": user.username}), 200
    return jsonify({"message": "Login yoki parol xato!"}), 401

@app.route('/api/messages', methods=['GET'])
def get_messages():
    u1 = request.args.get('user1')
    u2 = request.args.get('user2') # Bu shaxs yoki guruh nomi bo'lishi mumkin
    
    is_entity = Entity.query.filter_by(name=u2).first()
    
    if is_entity:
        # Guruh xabarlarini olish
        msgs = Message.query.filter_by(receiver=u2).order_by(Message.timestamp.asc()).all()
    else:
        # Shaxsiy xabarlarni filtrlab olish
        msgs = Message.query.filter(
            ((Message.sender == u1) & (Message.receiver == u2)) |
            ((Message.sender == u2) & (Message.receiver == u1))
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

# --- SOCKET.IO REAL-TIME ---

@socketio.on('join')
def handle_join(data):
    username = data.get('username')
    join_room(username)
    # Foydalanuvchi a'zo bo'lgan guruhlarga ham ulanish
    entities = Entity.query.filter(Entity.members.contains(username)).all()
    for ent in entities:
        join_room(ent.name)

@socketio.on('send_message')
def handle_send(data):
    # Bloklash tekshiruvi (faqat shaxsiy suhbatlarda)
    if not Entity.query.filter_by(name=data['receiver']).first():
        if user_is_blocked_by(data['receiver'], data['sender']):
            return # Xabar qabul qilinmaydi

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
    
    # Xabarni xonaga tarqatish (Room bu yerda receiver nomi)
    emit('receive_message', data, to=data['receiver'])
    # Agar receiver o'zi bo'lmasa, o'ziga ham yuboramiz (boshqa qurilmalar uchun)
    if data['receiver'] != data['sender']:
        emit('receive_message', data, to=data['sender'])

@socketio.on('edit_message')
def handle_edit(data):
    msg = Message.query.get(data['id'])
    if msg and msg.sender == data.get('sender', msg.sender):
        msg.content = data['content']
        msg.is_edited = True
        db.session.commit()
        emit('message_edited', {"id": data['id'], "content": data['content']}, to=data['receiver'])
        emit('message_edited', {"id": data['id'], "content": data['content']}, to=msg.sender)

@socketio.on('delete_message')
def handle_delete(data):
    msg = Message.query.get(data['id'])
    if msg:
        r, s = msg.receiver, msg.sender
        db.session.delete(msg)
        db.session.commit()
        emit('message_deleted', data['id'], to=r)
        emit('message_deleted', data['id'], to=s)

@socketio.on('create_entity')
def handle_create_entity(data):
    name = data.get('name')
    entity_type = data.get('type')
    creator = data.get('creator')

    # Bo‘sh nom yoki noto‘g‘ri tur
    if not name or entity_type not in ['group', 'channel']:
        return

    # Bunday guruh yoki kanal allaqachon bormi?
    if Entity.query.filter_by(name=name).first():
        emit('entity_error', {"message": "Bu nom band!"}, to=creator)
        return

    # Foydalanuvchini a'zo sifatida yozamiz
    new_entity = Entity(
        name=name,
        creator=creator,
        entity_type=entity_type,
        members=creator  # Yaratgan odam avtomatik a'zo bo‘ladi
    )

    db.session.add(new_entity)
    db.session.commit()

    # Xonaga qo‘shib qo‘yamiz
    join_room(name)

    # Front-endga qaytariladigan event
    emit('entity_created', {
        "name": name,
        "type": entity_type,
        "creator": creator
    }, broadcast=True)

@socketio.on('add_member')
def add_member(data):
    group = Entity.query.filter_by(name=data['group']).first()
    if not group:
        return
    
    members = group.members.split(',') if group.members else []
    if data['username'] not in members:
        members.append(data['username'])
        group.members = ",".join(members)
        db.session.commit()

    join_room(data['group'])
    emit('member_added', data, to=data['group'])

@socketio.on('block_user')
def handle_block(data):
    user = User.query.filter_by(username=data['sender']).first()
    if user:
        blocks = user.blocked_users.split(',') if user.blocked_users else []
        if data['target'] not in blocks:
            blocks.append(data['target'])
            user.blocked_users = ",".join(blocks)
            db.session.commit()

@socketio.on('call_signal')
def handle_call(data):
    emit('incoming_call', data, to=data['to'])

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, host='0.0.0.0', port=port)
