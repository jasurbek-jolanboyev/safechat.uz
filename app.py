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

# Fayl yuklash sozlamalari
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB maksimal
app.config['REELS_UPLOAD_FOLDER'] = os.path.join(app.config['UPLOAD_FOLDER'], 'reels')

# Ruxsat etilgan video formatlari (Reels uchun)
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm'}

# Papkalarni yaratish
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'media'), exist_ok=True)
os.makedirs(app.config['REELS_UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
CORS(app, resources={r"/*": {"origins": "*"}})

# --- MA'LUMOTLAR BAZASI MODELLARI ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=True)
    avatar = db.Column(db.Text, nullable=True)
    is_blocked = db.Column(db.Boolean, default=False)
    is_online = db.Column(db.Boolean, default=False)
    blocked_users = db.Column(db.Text, default="")
    bio = db.Column(db.String(200), default="Hello! I am using SafeChat.")
    devices = db.Column(db.Text, default="[]")

class Follow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    following_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('follower_id', 'following_id', name='unique_follow'),
    )

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
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(20))  # 'group' yoki 'channel'
    creator = db.Column(db.String(100))
    image = db.Column(db.String(200), default='https://ui-avatars.com/api/?name=G&background=random')
    # Dizayn sozlamalari (rang, gradient va hokazo)
    theme_color = db.Column(db.String(50), default='from-blue-500 to-indigo-600')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class EntityMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entity_id = db.Column(db.Integer, db.ForeignKey('entity.id'))
    username = db.Column(db.String(100))
    role = db.Column(db.String(20), default='member') # 'admin' yoki 'member'

# --- API YO'NALISHLARI ---
@app.route('/api/create_entity', methods=['POST'])
def create_entity():
    data = request.json
    name = data.get('name')
    etype = data.get('type')
    username = data.get('username')

    if not name:
        return jsonify({"status": "error", "message": "Nom kiritilmagan"}), 400

    new_ent = Entity(
        name=name, 
        type=etype, 
        creator=username,
        theme_color='from-purple-600 to-blue-500' if etype == 'group' else 'from-orange-500 to-red-500'
    )
    db.session.add(new_ent)
    db.session.commit()
    return jsonify({"status": "success", "name": name})

@app.route('/api/entities')
def get_entities():
    # Barcha ommaviy guruh/kanallarni olish
    ents = Entity.query.all()
    return jsonify([{
        "id": e.id, "name": e.name, "type": e.type, 
        "member_count": EntityMember.query.filter_by(entity_id=e.id).count()
    } for e in ents])

class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), default='new')  # new, accepted, rejected

# Bazani yaratish (barcha modellar qo'shilgandan keyin)
with app.app_context():
    db.create_all()

# --- YORDAMCHI FUNKSIYALAR ---
def user_is_blocked_by(target_username, sender_username):
    """Target foydalanuvchi senderni bloklaganmi?"""
    user = User.query.filter_by(username=target_username).first()
    if user and user.blocked_users:
        return sender_username in user.blocked_users.split(',')
    return False

def allowed_file(filename):
    """Faylning kengaytmasi ruxsat etilganmi?"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- STATIC FAYLLARNI XIZMAT QILISH ---
@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('', path)

@app.route('/user/<username>')
def show_user_profile(username):
    return send_from_directory('', 'index.html')

@app.errorhandler(404)
def not_found(e):
    return send_from_directory('', 'index.html')

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory(os.getcwd(), 'manifest.json')

@app.route('/logo.png')
def serve_logo():
    return send_from_directory(os.getcwd(), 'logo.png')

@app.route('/sw.js')
def serve_sw():
    return send_from_directory(os.getcwd(), 'sw.js')

@app.route('/uploads/<path:folder>/<path:filename>')
def serve_uploads(folder, filename):
    directory = os.path.join(app.config['UPLOAD_FOLDER'], folder)
    return send_from_directory(directory, filename)

# --- ASOSIY SAHIFA ---
@app.route('/')
def index():
    return "SafeChat V3 Server is Running!"

@app.route('/api/user/profile/<username>', methods=['GET'])
def get_user_profile(username):
    viewer_username = request.args.get('viewer')
    if not viewer_username:
        return jsonify({"error": "Viewer talab qilinadi"}), 400

    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"error": "Foydalanuvchi topilmadi"}), 404

    viewer = User.query.filter_by(username=viewer_username).first()
    if not viewer:
        return jsonify({"error": "Viewer topilmadi"}), 404

    # Bloklanganmi?
    blocked_users = user.blocked_users.split(',') if user.blocked_users else []
    is_blocked_by_viewer = viewer_username in blocked_users

    return jsonify({
        "username": user.username,
        "full_name": user.username,  # agar full_name bo'lmasa
        "bio": user.bio,
        "avatar": user.avatar or f"https://ui-avatars.com/api/?name={user.username}",
        "phone": user.phone if viewer_username == username else None,
        "posts_count": posts_count,
        "followers_count": followers_count,
        "following_count": following_count,
        "is_following": is_following,
        "is_blocked_by_viewer": is_blocked_by_viewer
    })

@app.route('/api/user/follow', methods=['POST'])
def follow_user():
    data = request.json
    viewer_username = data['viewer']
    target_username = data['target']
    action = data['action']  # 'follow' yoki 'unfollow'

    viewer = User.query.filter_by(username=viewer_username).first()
    target = User.query.filter_by(username=target_username).first()

    if not viewer or not target:
        return jsonify({"error": "Foydalanuvchi topilmadi"}), 404

    if action == 'follow':
        if viewer.id == target.id:
            return jsonify({"error": "O'zingizni follow qila olmaysiz"}), 400

        existing = Follow.query.filter_by(
            follower_id=viewer.id,
            following_id=target.id
        ).first()

        if not existing:
            new_follow = Follow(
                follower_id=viewer.id,
                following_id=target.id
            )
            db.session.add(new_follow)
            db.session.commit()

            # Realtime yangilash
            socketio.emit('follow_update', {
                'target': target_username,
                'followers_count': Follow.query.filter_by(following_id=target.id).count()
            }, broadcast=True)

            return jsonify({"success": True, "action": "followed"})

    elif action == 'unfollow':
        follow = Follow.query.filter_by(
            follower_id=viewer.id,
            following_id=target.id
        ).first()

        if follow:
            db.session.delete(follow)
            db.session.commit()

            socketio.emit('follow_update', {
                'target': target_username,
                'followers_count': Follow.query.filter_by(following_id=target.id).count()
            }, broadcast=True)

            return jsonify({"success": True, "action": "unfollowed"})

    return jsonify({"error": "Amal bajarilmadi"}), 400

@app.route('/api/user/block', methods=['POST'])
def block_user():
    data = request.json
    viewer_username = data['viewer']
    target_username = data['target']
    action = data['action']  # 'block' yoki 'unblock'

    target = User.query.filter_by(username=target_username).first()
    if not target:
        return jsonify({"error": "Foydalanuvchi topilmadi"}), 404

    blocked = target.blocked_users.split(',') if target.blocked_users else []

    if action == 'block':
        if viewer_username not in blocked:
            blocked.append(viewer_username)
            target.blocked_users = ','.join(blocked)
    else:
        if viewer_username in blocked:
            blocked.remove(viewer_username)
            target.blocked_users = ','.join(blocked) if blocked else ''

    db.session.commit()

    # Realtime yangilash (ixtiyoriy)
    socketio.emit('block_update', {
        'target': target_username,
        'blocked_by': viewer_username,
        'is_blocked': action == 'block'
    }, broadcast=True)

    return jsonify({"success": True})

# --- API ENDPOINTLAR ---
@app.route('/api/recent_chats', methods=['GET'])
def get_recent_chats():
    username = request.args.get('username')
    if not username:
        return jsonify([]), 400
    msgs = Message.query.filter(
        (Message.sender == username) | (Message.receiver == username)
    ).order_by(Message.timestamp.desc()).all()
    contacts = []
    seen = set()
    for m in msgs:
        other_user = m.sender if m.sender != username else m.receiver
        if other_user not in seen:
            contacts.append(other_user)
            seen.add(other_user)
    return jsonify(contacts)

@app.route('/api/register', methods=['POST'])
def register_api():
    data = request.json
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
        if not data:
            return jsonify({"message": "Ma'lumot yuborilmadi"}), 400
        u_name = str(data.get('username', '')).strip()
        p_word = str(data.get('password', '')).strip()
        if not u_name or not p_word:
            return jsonify({"message": "Username va parolni to'ldiring"}), 400
        user = User.query.filter_by(username=u_name).first()
        if not user and not u_name.endswith('.connect.uz'):
            user = User.query.filter_by(username=u_name + '.connect.uz').first()
        if user:
            if check_password_hash(user.password, p_word):
                user.is_online = True
                try:
                    db.session.commit()
                except Exception as db_err:
                    db.session.rollback()
                    print(f"Database commit error: {db_err}")
                return jsonify({
                "status": "success",
                "username": user.username,
                "phone": user.phone,
                "avatar": user.avatar or f"https://ui-avatars.com/api/?name={user.username}",
                "bio": user.bio or "Bio hali yozilmagan",
                "full_name": user.username  # Hozircha username ni full_name sifatida ishlatamiz
            }), 200
            else:
                return jsonify({"message": "Kiritilgan parol noto'g'ri!"}), 401
        else:
            return jsonify({"message": "Bunday foydalanuvchi mavjud emas!"}), 401
    except Exception as e:
        print(f"LOGIN_CRITICAL_ERROR: {e}")
        return jsonify({"message": "Serverda texnik xatolik yuz berdi"}), 500



# Typing indikatori (chatda yozish boshlanganda)
@socketio.on('typing_start')
def handle_typing_start(data):
    sender = data['sender']
    receiver = data['receiver']
    
    # Faqat shu suhbatdoshga yuboriladi
    emit('user_typing', {
        'sender': sender,
        'is_typing': True
    }, room=receiver)

# Typing to‘xtaganida
@socketio.on('typing_stop')
def handle_typing_stop(data):
    sender = data['sender']
    receiver = data['receiver']
    
    emit('user_typing', {
        'sender': sender,
        'is_typing': False
    }, room=receiver)

# QOLGAN BARCHA ENDPOINTLAR (HECH QAYSI O‘CHIRILMAGAN)
@app.route('/api/admin/edit_user', methods=['POST'])
def admin_edit_user():
    data = request.json
    if data.get('admin') != 'admin':
        return jsonify({"message": "Ruxsat yo'q"}), 403
    user = User.query.filter_by(username=data.get('target')).first()
    if user:
        user.username = data.get('name', user.username)
        user.bio = data.get('bio', user.bio)
        db.session.commit()
        socketio.emit('user_update', {
            "userId": user.username,
            "updatedFields": {"name": user.username, "bio": user.bio}
        })
        return jsonify({"status": "success"})
    return jsonify({"message": "User topilmadi"}), 404

@app.route('/api/admin/delete_user', methods=['POST'])
def delete_user_admin():
    data = request.json
    if data.get('admin') != 'admin':
        return jsonify({"m": "No"}), 403
    user = User.query.filter_by(username=data.get('target')).first()
    if user:
        Message.query.filter(
            (Message.sender == user.username) | (Message.receiver == user.username)
        ).delete()
        db.session.delete(user)
        db.session.commit()
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 404

@socketio.on('admin_broadcast')
def handle_broadcast(data):
    admin_user = data.get('sender')
    content = data.get('message')
    emit('receive_admin_notification', {
        'title': '📢 Tizim E\'loni',
        'message': content,
        'sender': 'Admin',
        'timestamp': datetime.utcnow().strftime('%H:%M')
    }, broadcast=True)

@app.route('/api/admin/block', methods=['POST'])
def admin_block_user():
    data = request.json
    user = User.query.filter_by(username=data['username']).first()
    if user:
        user.is_blocked = not user.is_blocked
        db.session.commit()
        return jsonify({"message": "Muvaffaqiyatli!"}), 200
    return jsonify({"message": "User topilmadi"}), 404

@socketio.on('admin_action')
def handle_admin_action(data):
    action = data.get('action')
    target = data.get('target')
    if action == 'ban':
        emit('user_banned', {'target': target}, broadcast=True)

@app.route('/api/messages', methods=['GET'])
def get_messages():
    u1 = request.args.get('user1')
    u2 = request.args.get('user2')
    if not u1 or not u2:
        return jsonify({"message": "Foydalanuvchilar ko'rsatilmadi"}), 400
    try:
        is_entity = Entity.query.filter_by(name=u2).first()
        if is_entity:
            msgs = Message.query.filter_by(receiver=u2).order_by(Message.timestamp.asc()).all()
        else:
            msgs = Message.query.filter(
                ((Message.sender == u1) & (Message.receiver == u2)) |
                ((Message.sender == u2) & (Message.receiver == u1))
            ).order_by(Message.timestamp.asc()).all()
        
        result = []
        for m in msgs:
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
                "is_edited": getattr(m, 'is_edited', False),
                "timestamp": m.timestamp.strftime('%H:%M'),
                "reply_to": reply_to_obj
            })
        return jsonify(result)
    except Exception as e:
        print(f"Xabarlarni yuklashda xato: {e}")
        return jsonify({"message": "Xabarlarni yuklab bo'lmadi"}), 500

@app.route('/api/apply', methods=['POST'])
def submit_apply():
    data = request.json
    new_app = Application(
        name=data['name'],
        phone=data['phone'],
        reason=data['reason']
    )
    db.session.add(new_app)
    db.session.commit()
    return jsonify({"status": "success"})

@app.route('/api/admin/stats')
def get_admin_stats():
    total_users = User.query.count()
    total_messages = Message.query.count()
    total_groups = Entity.query.filter_by(type='group').count()
    online_now = User.query.filter_by(is_online=True).count()
    return jsonify({
        "total": total_users,
        "online": online_now,
        "messages": total_messages,
        "groups": total_groups,
        "server_time": datetime.utcnow().strftime('%H:%M:%S')
    })

@app.route('/api/update_profile', methods=['POST'])
def update_profile():
    data = request.json
    user = User.query.filter_by(username=data.get('username')).first()
    if user:
        field = data.get('field')
        value = data.get('value')
        if field == 'name': user.username = value
        if field == 'bio': user.bio = value
        db.session.commit()
        return jsonify({"success": True})
    return jsonify({"success": False}), 404

@app.route('/api/update_user', methods=['POST'])
def update_user():
    data = request.json
    user = User.query.filter_by(username=data.get('old_username')).first()
    if not user:
        return jsonify({"message": "User topilmadi"}), 404
    new_username = data.get('new_username')
    if User.query.filter_by(username=new_username).first():
        return jsonify({"message": "Bu username band!"}), 400
    user.username = new_username
    db.session.commit()
    return jsonify({"status": "success"})

@app.route('/api/update_password', methods=['POST'])
def update_password():
    data = request.json
    user = User.query.filter_by(username=data.get('username')).first()
    if user:
        user.password = generate_password_hash(data.get('password'))
        db.session.commit()
        return jsonify({"status": "success"})
    return jsonify({"message": "Xato!"}), 400

@app.route('/api/users/search', methods=['GET'])
def search_users():
    users = User.query.all()
    return jsonify([{"username": u.username, "is_blocked": u.is_blocked} for u in users])

@app.route('/api/upload_avatar', methods=['POST'])
def upload_avatar():
    if 'file' not in request.files:
        return jsonify({"message": "Fayl topilmadi"}), 400
    file = request.files['file']
    u_name = request.form.get('username')
    if file and u_name:
        filename = secure_filename(f"avatar_{u_name}_{file.filename}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'media', filename)
        file.save(filepath)
        user = User.query.filter_by(username=u_name).first()
        user.avatar = f"/uploads/media/{filename}"
        db.session.commit()
        return jsonify({"status": "success", "url": user.avatar})
    return jsonify({"message": "Xato"}), 400


@socketio.on('join')
def handle_join(data):
    username = data.get('username')
    if not username:
        return

    join_room(username)

    memberships = EntityMember.query.filter_by(username=username).all()
    for m in memberships:
        entity = Entity.query.get(m.entity_id)
        if entity:
            join_room(entity.name)

    print(f"✅ {username} barcha xonalarga ulandi")


@socketio.on('send_message')
def handle_send(data):
    try:
        sender_u = data.get('sender')
        receiver_u = data.get('receiver')
        msg_type = data.get('type', 'text')
        content = data.get('content', '')
        
        is_entity = Entity.query.filter_by(name=receiver_u).first()
        if not is_entity:
            if user_is_blocked_by(receiver_u, sender_u):
                print(f"BLOCKED: {sender_u} -> {receiver_u}")
                return
        
        reply_json = json.dumps(data.get('reply_to')) if data.get('reply_to') else None
        new_msg = Message(
            sender=sender_u,
            receiver=receiver_u,
            content=content,
            msg_type=msg_type,
            reply_info=reply_json
        )
        db.session.add(new_msg)
        db.session.commit()
        
        data['id'] = new_msg.id
        data['timestamp'] = datetime.utcnow().strftime('%H:%M')
        
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
    entity_type = data.get('type')
    creator = data.get('creator')
    if Entity.query.filter_by(name=name).first():
        emit('entity_error', {"message": "Bu nom band!"}, to=creator)
        return
    new_entity = Entity(
        name=name,
        creator=creator,
        entity_type=entity_type,
        members=creator  # Yangi qo'shilgan: members ga creator qo'shiladi
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
    emit('incoming_call', data, to=data['to'])

@socketio.on('disconnect')
def handle_disconnect():
    print("Foydalanuvchi tarmoqdan uzildi")

# Qolgan socket eventlar va route’lar (sizning asl kodingizdagi qolgan qismlar)
# Masalan:
@app.route('/api/search', methods=['GET'])
def search_entities():
    query = request.args.get('q', '').strip().lower()
    if not query:
        return jsonify([])
    results = []
    users = User.query.filter(User.username.ilike(f'%{query}%')).limit(10).all()
    for u in users:
        results.append({
            "display_name": u.username,
            "subtext": "SafeChat foydalanuvchisi",
            "type": "user",
            "avatar_name": u.username
        })
    entities = Entity.query.filter(Entity.name.ilike(f'%{query}%')).limit(10).all()
    for e in entities:
        m_count = len(e.members.split(',')) if e.members else 0
        results.append({
            "display_name": e.name,
            "subtext": f"{m_count} a'zolar",
            "type": e.entity_type,
            "avatar_name": e.name
        })
    return jsonify(results)

@app.route('/api/delete_entity', methods=['POST'])
def delete_entity():
    data = request.json
    username = data.get('username')
    target = data.get('target')
    target_type = data.get('type')
    try:
        if target_type == 'chat':
            Message.query.filter(
                ((Message.sender == username) & (Message.receiver == target)) |
                ((Message.sender == target) & (Message.receiver == username))
            ).delete()
            db.session.commit()
            return jsonify({"success": True, "message": "Chat o'chirildi"})
        elif target_type == 'group':
            entity = Entity.query.filter_by(name=target).first()
            if entity:
                members = entity.members.split(',')
                if username in members:
                    members.remove(username)
                    entity.members = ",".join(members)
                    db.session.commit()
                    return jsonify({"success": True, "message": "Guruhdan chiqdingiz"})
            return jsonify({"success": False, "message": "Guruh topilmadi"}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@socketio.on('update_user_profile')
def handle_profile_update(data):
    username = data.get('username')
    new_name = data.get('name')
    new_bio = data.get('bio')
    user = User.query.filter_by(username=username).first()
    if user:
        # Username o'zgarmasligi uchun faqat bio yangilanadi
        # Agar username o'zgartirish kerak bo'lsa, alohida endpoint ishlatiladi
        user.bio = new_bio
        db.session.commit()
        emit('profile_updated_success', {
            "name": user.username,  # username o'zgarmaydi
            "bio": new_bio
        }, broadcast=False)


# Postlar uchun media papkasi
POSTS_FOLDER = os.path.join('uploads', 'posts')
os.makedirs(POSTS_FOLDER, exist_ok=True)

# app.py ichiga qo'shing
@app.route('/api/news/view/<int:post_id>', methods=['POST'])
def update_post_view(post_id):
    post = Post.query.get(post_id)
    if post:
        post.views += 1
        db.session.commit()
        # Barcha foydalanuvchilarga yangi ko'rishlar sonini yuborish
        socketio.emit('update_views', {'post_id': post_id, 'views': post.views})
        return jsonify({"status": "success", "new_views": post.views})
    return jsonify({"status": "error"}), 404

# Modelni yangilash
class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200))
    description = db.Column(db.Text)
    media_urls = db.Column(db.JSON)  # ['uploads/posts/1.jpg', ...]
    post_type = db.Column(db.String(50)) # 'video', 'image', 'reels', 'audio'
    views = db.Column(db.Integer, default=0) # KO'RISHLAR SONI
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
@app.route('/api/news-feed')
def get_news_feed():
    posts = Post.query.order_by(Post.created_at.desc()).all()
    output = []
    for post in posts:
        output.append({
            "id": post.id,
            "type": post.post_type,
            "title": post.title,
            "description": post.description,
            "media": post.media_urls,
        })
    return jsonify(output)

@app.route('/api/logout', methods=['POST'])
def logout_api():
    data = request.json
    username = data.get('username')
    if username:
        user = User.query.filter_by(username=username).first()
        if user:
            user.is_online = False
            db.session.commit()
            socketio.emit('user_disconnected', {'username': username}, broadcast=True)
    return jsonify({"success": True})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, host='0.0.0.0', port=port)