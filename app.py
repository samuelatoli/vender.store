from flask import Flask, request, redirect, url_for, render_template, send_from_directory, flash, session
import sqlite3
import os
import uuid
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
DB_PATH = os.path.join(BASE_DIR, 'products.db')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
app.secret_key = 'change-me'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        contact TEXT,
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

    # Add legacy schema migration support for older databases.
    user_columns = [row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()]
    if 'is_admin' not in user_columns:
        c.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0')

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
    conn = get_db_connection()
    products = conn.execute('SELECT id, name, description, contact, price, image FROM products ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('list.html', products=products)


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
        conn.execute('''INSERT INTO products (name, description, contact, price, image)
                     VALUES (?, ?, ?, ?, ?)''', (name, description, contact, price_val, filename))
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
