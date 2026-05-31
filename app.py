from flask import Flask, request, redirect, url_for, render_template, send_from_directory, flash
import sqlite3, os
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
DB_PATH = os.path.join(BASE_DIR, 'products.db')
ALLOWED_EXT = {'png','jpg','jpeg','gif'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = 'change-me'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY,
        name TEXT,
        description TEXT,
        contact TEXT,
        price REAL,
        image TEXT
    )''')
    conn.commit()
    conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

@app.route('/')
def index():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, name, description, contact, price, image FROM products ORDER BY id DESC')
    products = c.fetchall()
    conn.close()
    return render_template('list.html', products=products)

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        contact = request.form.get('contact')
        price = request.form.get('price')
        file = request.files.get('image')
        filename = None
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        try:
            price_val = float(price) if price else 0.0
        except:
            price_val = 0.0
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT INTO products (name, description, contact, price, image)
                     VALUES (?, ?, ?, ?, ?)''', (name, description, contact, price_val, filename))
        conn.commit()
        conn.close()
        flash('Product uploaded')
        return redirect(url_for('index'))
    return render_template('upload.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_db()
    app.run(debug=True)
