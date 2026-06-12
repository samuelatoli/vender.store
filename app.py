from flask import Flask, request, redirect, url_for, render_template, send_from_directory, flash, session
import sqlite3
import os
import uuid
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import smtplib
from email.message import EmailMessage

try:
    import openai
except ImportError:
    openai = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
DB_PATH = os.path.join(BASE_DIR, 'products.db')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
app.secret_key = 'change-me'

# Initialize SocketIO only if package is available
try:
    from flask_socketio import SocketIO, join_room, leave_room, emit
    socketio = SocketIO(app, cors_allowed_origins='*')
except Exception:
    socketio = None

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        contact TEXT,
        website TEXT,
        social_links TEXT,
        uploader_id INTEGER,
        price REAL,
        image TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS payment_methods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        details TEXT,
        active INTEGER NOT NULL DEFAULT 1
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        buyer_name TEXT NOT NULL,
        buyer_contact TEXT NOT NULL,
        payment_method_id INTEGER,
        note TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(product_id) REFERENCES products(id),
        FOREIGN KEY(payment_method_id) REFERENCES payment_methods(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,
        user_id INTEGER,
        name TEXT,
        email TEXT,
        message TEXT NOT NULL,
        sender TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Add legacy schema migration support for older databases.
    user_columns = [row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()]
    if 'is_admin' not in user_columns:
        c.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0')

    product_columns = [row[1] for row in c.execute("PRAGMA table_info(products)").fetchall()]
    if 'website' not in product_columns:
        c.execute('ALTER TABLE products ADD COLUMN website TEXT')
    if 'social_links' not in product_columns:
        c.execute('ALTER TABLE products ADD COLUMN social_links TEXT')
    if 'uploader_id' not in product_columns:
        c.execute('ALTER TABLE products ADD COLUMN uploader_id INTEGER')

    conn.commit()
    conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_uploaded_image(file):
    if not file or file.filename == '':
        return None
    if allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        file.save(path)
        return unique_name
    return None


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    search_query = request.args.get('q', '').strip()
    conn = get_db_connection()
    if search_query:
        like_pattern = f'%{search_query}%'
        products = conn.execute(
            'SELECT id, name, description, contact, website, social_links, uploader_id, price, image FROM products '
            'WHERE name LIKE ? OR description LIKE ? OR contact LIKE ? OR website LIKE ? OR social_links LIKE ? '
            'ORDER BY id DESC',
            (like_pattern, like_pattern, like_pattern, like_pattern, like_pattern)
        ).fetchall()
    else:
        products = conn.execute('SELECT id, name, description, contact, website, social_links, uploader_id, price, image FROM products ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('list.html', products=products, query=search_query)


@app.route('/product/<int:product_id>')
def product_detail(product_id):
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
    payment_methods = conn.execute('SELECT name, details FROM payment_methods WHERE active = 1 ORDER BY id DESC').fetchall()
    conn.close()
    if product is None:
        flash('Product not found.')
        return redirect(url_for('index'))
    return render_template('detail.html', product=product, payment_methods=payment_methods)

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        contact = request.form.get('contact', '').strip()
        website = request.form.get('website', '').strip()
        social_links = request.form.get('social_links', '').strip()
        price = request.form.get('price', '').strip()
        file = request.files.get('image')
        if not name:
            flash('Product name is required.')
            return redirect(url_for('upload'))

        filename = save_uploaded_image(file)
        try:
            price_val = float(price) if price else 0.0
        except ValueError:
            flash('Price must be a number.')
            return redirect(url_for('upload'))

        conn = get_db_connection()
        uploader = session.get('user_id')
        conn.execute('''INSERT INTO products (name, description, contact, website, social_links, uploader_id, price, image)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (name, description, contact, website or None, social_links or None, uploader, price_val, filename))
        conn.commit()
        conn.close()
        flash('Product uploaded successfully.')
        return redirect(url_for('index'))
    return render_template('upload.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if not email or not password:
            flash('Email and password are required.')
            return redirect(url_for('signup'))
        # basic email validation
        if '@' not in email:
            flash('Please provide a valid email address.')
            return redirect(url_for('signup'))
        hashed = generate_password_hash(password)
        conn = get_db_connection()
        admin_exists = conn.execute('SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1').fetchone()
        is_admin = 0 if admin_exists else 1
        try:
            conn.execute('INSERT INTO users (email, password, is_admin) VALUES (?, ?, ?)', (email, hashed, is_admin))
            conn.commit()
        except sqlite3.IntegrityError:
            flash('Email already taken.')
            conn.close()
            return redirect(url_for('signup'))
        conn.close()
        if is_admin:
            flash('Account created. Please log in. Admin access configured.')
        else:
            flash('Account created. Please log in.')
        return redirect(url_for('login'))
    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        conn = get_db_connection()
        user = conn.execute('SELECT id, email, password, is_admin FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['email'] = user['email']
            session['is_admin'] = bool(user['is_admin'])
            flash('Logged in successfully.')
            return redirect(url_for('index'))
        flash('Invalid email or password.')
        return redirect(url_for('login'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('email', None)
    session.pop('is_admin', None)
    flash('Logged out.')
    return redirect(url_for('index'))


def user_is_admin():
    user_id = session.get('user_id')
    if not user_id:
        return False
    conn = get_db_connection()
    user = conn.execute('SELECT is_admin FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return bool(user and user['is_admin'])


def get_product_contact(product_id):
    try:
        pid = int(product_id)
    except Exception:
        return None
    conn = get_db_connection()
    row = conn.execute('SELECT contact FROM products WHERE id = ?', (pid,)).fetchone()
    conn.close()
    return row['contact'] if row else None


def get_ai_response(prompt, user_email=None):
    prompt_text = prompt.strip()
    if not prompt_text:
        return 'Please enter a question so I can help you.'

    api_key = os.environ.get('OPENAI_API_KEY')
    if openai and api_key:
        try:
            openai.api_key = api_key
            model = os.environ.get('OPENAI_MODEL', 'gpt-3.5-turbo')
            completion = openai.ChatCompletion.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': 'You are a helpful assistant for Vender Store. Answer politely and concisely.'},
                    {'role': 'user', 'content': prompt_text}
                ],
                max_tokens=250,
                temperature=0.7,
            )
            return completion.choices[0].message.content.strip()
        except Exception as exc:
            app.logger.exception('OpenAI request failed: %s', exc)

    lower_prompt = prompt_text.lower()
    if 'price' in lower_prompt or 'cost' in lower_prompt:
        return 'I can help explain pricing and buying steps. Please check the product page for vendor contact details, and request payment through the product page form.'
    if 'upload' in lower_prompt or 'list' in lower_prompt or 'product' in lower_prompt:
        return 'To add a product, go to Upload, fill in the product name, description, contact, price, and attach an image if you like.'
    if 'payment' in lower_prompt or 'buy' in lower_prompt or 'purchase' in lower_prompt:
        return 'Use the product page to request payment. Choose an available payment method and send your contact details to the vendor.'
    if 'admin' in lower_prompt or 'support' in lower_prompt:
        return 'Support is available through the Chat page. You can also create an account and contact the administrator directly if you need help.'
    return 'Welcome to Vender Store AI support. Ask about products, uploading items, payments, or how the site works.'


@app.route('/ai-chat')
def ai_chat():
    return render_template('ai_chat.html')


@app.route('/ai-chat/message', methods=['POST'])
def ai_chat_message():
    data = request.get_json(silent=True)
    if not data or 'message' not in data:
        return {'error': 'Message text is required.'}, 400
    message = str(data.get('message', '')).strip()
    if not message:
        return {'error': 'Message text is required.'}, 400
    response_text = get_ai_response(message, session.get('email'))
    return {'response': response_text}


if socketio:
    @socketio.on('ai_chat_message')
    def handle_ai_chat_message(data):
        message = str(data.get('message', '')).strip()
        if not message:
            emit('ai_chat_response', {'response': 'Please enter a message before sending.'})
            return
        response_text = get_ai_response(message, session.get('email'))
        emit('ai_chat_response', {'response': response_text})
    @socketio.on('join_room')
    def handle_join(data):
        room = data.get('room')
        if room:
            join_room(room)

    @socketio.on('leave_room')
    def handle_leave(data):
        room = data.get('room')
        if room:
            leave_room(room)

    @socketio.on('send_room_message')
    def handle_send_room_message(data):
        room = data.get('room')
        product_id = data.get('product_id')
        name = data.get('name')
        email = data.get('email')
        message = data.get('message')
        sender = data.get('sender', 'user')
        # persist message
        try:
            conn = get_db_connection()
            conn.execute('INSERT INTO messages (product_id, user_id, name, email, message, sender) VALUES (?, ?, ?, ?, ?, ?)', (product_id, session.get('user_id'), name, email, message, sender))
            conn.commit()
            conn.close()
        except Exception:
            pass
        emit('room_message', {'product_id': product_id, 'name': name, 'email': email, 'message': message, 'sender': sender}, room=room)


def send_email_notification(to_addr, subject, body):
    # If ADMIN_EMAIL or SMTP settings are not configured, log the message instead
    if not to_addr:
        app.logger.info('Email not sent (no recipient configured): %s', subject)
        return
    host = os.environ.get('MAIL_HOST')
    port = int(os.environ.get('MAIL_PORT', '587'))
    username = os.environ.get('MAIL_USERNAME')
    password = os.environ.get('MAIL_PASSWORD')
    use_tls = os.environ.get('MAIL_USE_TLS', '1') in ('1', 'True', 'true')

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = username or f'no-reply@{os.uname().nodename}' if hasattr(os, 'uname') else (username or 'no-reply')
    msg['To'] = to_addr
    msg.set_content(body)

    if not host or not username or not password:
        app.logger.info('Email (simulated) to %s: %s\n%s', to_addr, subject, body)
        return

    try:
        if use_tls:
            server = smtplib.SMTP(host, port)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(host, port)
        server.login(username, password)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        app.logger.exception('Failed to send email: %s', e)


@app.route('/product/<int:product_id>/buy', methods=['POST'])
def buy_product(product_id):
    name = request.form.get('buyer_name', '').strip()
    contact = request.form.get('buyer_contact', '').strip()
    payment_method_id = request.form.get('payment_method')
    note = request.form.get('note', '').strip()

    if not name or not contact:
        flash('Name and contact are required to request payment.')
        return redirect(url_for('product_detail', product_id=product_id))

    try:
        pm_id = int(payment_method_id) if payment_method_id else None
    except ValueError:
        pm_id = None

    conn = get_db_connection()
    conn.execute('''INSERT INTO transactions (product_id, buyer_name, buyer_contact, payment_method_id, note)
                 VALUES (?, ?, ?, ?, ?)''', (product_id, name, contact, pm_id, note))
    conn.commit()
    conn.close()
    flash('Purchase request sent to administrator. They will contact you to complete payment.')
    # notify admins via socket and send email
    try:
        if socketio:
            socketio.emit('new_transaction', {'product_id': product_id, 'buyer_name': name, 'buyer_contact': contact, 'note': note}, room='admin')
    except Exception:
        pass
    try:
        send_email_notification(os.environ.get('ADMIN_EMAIL'), 'New purchase request', f'Product {product_id}\nBuyer: {name} {contact}\nNote: {note}')
    except Exception:
        pass
    return redirect(url_for('product_detail', product_id=product_id))


@app.route('/product/<int:product_id>/delete', methods=['POST'])
def delete_product(product_id):
    conn = get_db_connection()
    product = conn.execute('SELECT id, image, uploader_id FROM products WHERE id = ?', (product_id,)).fetchone()
    if not product:
        conn.close()
        flash('Product not found.')
        return redirect(url_for('index'))

    allowed = False
    try:
        if user_is_admin():
            allowed = True
        elif session.get('user_id') and product['uploader_id'] == session.get('user_id'):
            allowed = True
    except Exception:
        allowed = False

    if not allowed:
        conn.close()
        flash('Permission denied.')
        return redirect(url_for('product_detail', product_id=product_id))

    # remove image file if exists
    if product['image']:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], product['image']))
        except Exception:
            pass

    conn.execute('DELETE FROM products WHERE id = ?', (product_id,))
    conn.commit()
    conn.close()
    flash('Product removed.')
    return redirect(url_for('index'))


@app.route('/admin/transactions')
def admin_transactions():
    if not user_is_admin():
        flash('Administrator access required.')
        return redirect(url_for('index'))
    conn = get_db_connection()
    rows = conn.execute('''SELECT t.id, t.product_id, p.name as product_name, t.buyer_name, t.buyer_contact, pm.name as payment_method, t.note, t.status, t.created_at
                           FROM transactions t
                           LEFT JOIN products p ON p.id = t.product_id
                           LEFT JOIN payment_methods pm ON pm.id = t.payment_method_id
                           ORDER BY t.created_at DESC''').fetchall()
    conn.close()
    return render_template('admin_transactions.html', transactions=rows)


@app.route('/chat', methods=['GET', 'POST'])
def chat():
    # public support chat; if logged in, prefill name/email
    conn = get_db_connection()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        product_id = request.form.get('product_id')
        message = request.form.get('message', '').strip()
        sender = 'user'
        user_id = session.get('user_id')
        if not message or not (name and email):
            flash('Name, email and message are required.')
            return redirect(url_for('chat'))
        try:
            pid = int(product_id) if product_id else None
        except ValueError:
            pid = None
        conn.execute('INSERT INTO messages (product_id, user_id, name, email, message, sender) VALUES (?, ?, ?, ?, ?, ?)', (pid, user_id, name, email, message, sender))
        conn.commit()
        # emit real-time event to admin channel and to user room
        try:
            if socketio:
                socketio.emit('new_message', {'email': email, 'name': name, 'message': message, 'sender': 'user'}, room='admin')
                socketio.emit('new_message', {'email': email, 'name': name, 'message': message, 'sender': 'user'}, room=email)
        except Exception:
            pass
        try:
            send_email_notification(os.environ.get('ADMIN_EMAIL'), 'New support message', f'From: {name} <{email}>\n\n{message}')
        except Exception:
            pass
        flash('Message sent. An administrator will reply.')
        return redirect(url_for('chat'))

    # show recent messages from this user email if present in session
    user_email = session.get('email')
    msgs = []
    if user_email:
        msgs = conn.execute('SELECT * FROM messages WHERE email = ? ORDER BY created_at DESC LIMIT 50', (user_email,)).fetchall()
    conn.close()
    return render_template('chat.html', messages=msgs, user_email=user_email)


@app.route('/admin/chats')
def admin_chats():
    if not user_is_admin():
        flash('Administrator access required.')
        return redirect(url_for('index'))
    conn = get_db_connection()
    rows = conn.execute('SELECT DISTINCT email, name FROM messages ORDER BY created_at DESC').fetchall()
    conn.close()
    return render_template('admin_chats.html', conversations=rows)


@app.route('/admin/chats/<email>', methods=['GET', 'POST'])
def admin_chat_view(email):
    if not user_is_admin():
        flash('Administrator access required.')
        return redirect(url_for('index'))
    conn = get_db_connection()
    if request.method == 'POST':
        # admin reply
        text = request.form.get('message', '').strip()
        if text:
            conn.execute('INSERT INTO messages (product_id, user_id, name, email, message, sender) VALUES (?, ?, ?, ?, ?, ?)', (None, session.get('user_id'), None, email, text, 'admin'))
            conn.commit()
            # emit to user room and notify via email
            try:
                if socketio:
                    socketio.emit('new_message', {'email': email, 'message': text, 'sender': 'admin'}, room=email)
            except Exception:
                pass
            try:
                send_email_notification(email, 'Admin reply', text)
            except Exception:
                pass
            flash('Reply sent.')
            return redirect(url_for('admin_chat_view', email=email))

    msgs = conn.execute('SELECT * FROM messages WHERE email = ? ORDER BY created_at ASC', (email,)).fetchall()
    conn.close()
    return render_template('admin_chat_view.html', email=email, messages=msgs)


@app.route('/admin/payments', methods=['GET', 'POST'])
def admin_payments():
    if not user_is_admin():
        flash('Administrator access required.')
        return redirect(url_for('index'))

    conn = get_db_connection()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        details = request.form.get('details', '').strip()
        active = 1 if request.form.get('active') == 'on' else 0
        if not name:
            flash('Payment method name is required.')
            return redirect(url_for('admin_payments'))
        conn.execute('INSERT INTO payment_methods (name, details, active) VALUES (?, ?, ?)', (name, details, active))
        conn.commit()
        flash('Payment method added.')
        conn.close()
        return redirect(url_for('admin_payments'))

    payment_methods = conn.execute('SELECT * FROM payment_methods ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('admin_payments.html', payment_methods=payment_methods)


@app.route('/admin/products', methods=['GET', 'POST'])
def admin_products():
    if not user_is_admin():
        flash('Administrator access required.')
        return redirect(url_for('index'))
    conn = get_db_connection()
    if request.method == 'POST':
        ids = request.form.getlist('selected')
        removed = 0
        for id_str in ids:
            try:
                pid = int(id_str)
            except Exception:
                continue
            prod = conn.execute('SELECT image FROM products WHERE id = ?', (pid,)).fetchone()
            if prod:
                if prod['image']:
                    try:
                        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], prod['image']))
                    except Exception:
                        pass
                conn.execute('DELETE FROM products WHERE id = ?', (pid,))
                removed += 1
        conn.commit()
        conn.close()
        flash(f'{removed} product(s) removed.')
        return redirect(url_for('admin_products'))

    rows = conn.execute('SELECT id, name, price, contact, website, uploader_id FROM products ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('admin_products.html', products=rows)


@app.route('/admin/payments/<int:method_id>/toggle')
def toggle_payment_method(method_id):
    if not user_is_admin():
        flash('Administrator access required.')
        return redirect(url_for('index'))
    conn = get_db_connection()
    conn.execute('UPDATE payment_methods SET active = 1 - active WHERE id = ?', (method_id,))
    conn.commit()
    conn.close()
    flash('Payment method status updated.')
    return redirect(url_for('admin_payments'))


@app.route('/admin/payments/<int:method_id>/delete')
def delete_payment_method(method_id):
    if not user_is_admin():
        flash('Administrator access required.')
        return redirect(url_for('index'))
    conn = get_db_connection()
    conn.execute('DELETE FROM payment_methods WHERE id = ?', (method_id,))
    conn.commit()
    conn.close()
    flash('Payment method deleted.')
    return redirect(url_for('admin_payments'))


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_db()
    app.run(debug=True)
