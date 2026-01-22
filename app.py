import eventlet
eventlet.monkey_patch()
import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'safechat_ultra_secure_2026_key'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///safechat_v3.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['REELS_UPLOAD_FOLDER'] = os.path.join(app.config['UPLOAD_FOLDER'], 'reels')
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm'}
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'media'), exist_ok=True)
os.makedirs(app.config['REELS_UPLOAD_FOLDER'], exist_ok=True)
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
CORS(app, resources={r"/*": {"origins": "*"}})

# Modellar (oldingi xabarda berilgan barcha modellar shu yerda bo'ladi)
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
    __table_args__ = (db.UniqueConstraint('follower_id', 'following_id', name='unique_follow'),)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(100), nullable=False)
    receiver = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    msg_type = db.Column(db.String(20), default='text')
    reply_info = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Entity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    type = db.Column(db.String(20), nullable=False)  # 'group' yoki 'channel'
    creator = db.Column(db.String(100), nullable=False)
    image = db.Column(db.String(200), default='https://ui-avatars.com/api/?name=G&background=random')
    theme_color = db.Column(db.String(50), default='from-blue-500 to-indigo-600')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class EntityMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entity_id = db.Column(db.Integer, db.ForeignKey('entity.id'))
    username = db.Column(db.String(100))
    role = db.Column(db.String(20), default='member')  # 'admin' yoki 'member'

class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), default='new')

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200))
    description = db.Column(db.Text)
    media_urls = db.Column(db.JSON)
    post_type = db.Column(db.String(50))
    views = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# Yordamchi funksiyalar
def user_is_blocked_by(target_username, sender_username):
    user = User.query.filter_by(username=target_username).first()
    if user and user.blocked_users:
        return sender_username in user.blocked_users.split(',')
    return False

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Static fayllar
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

@app.route('/')
def index():
    return "SafeChat V3 Server is Running!"

# Boshqa route'lar (register, login, etc.) oldingi xabarda berilgan barchasi shu yerda bo'ladi
# ... (barcha oldingi route'lar va socket hodisalari)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, host='0.0.0.0', port=port)