import sqlite3
import tempfile
import pytest
import app as app_module

# module-level references
app = app_module.app
init_db = app_module.init_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_products.db"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    monkeypatch.setattr(app_module, 'DB_PATH', str(db_file))
    monkeypatch.setattr(app_module, 'UPLOAD_FOLDER', str(upload_dir))
    app.config['UPLOAD_FOLDER'] = str(upload_dir)
    app.config['TESTING'] = True

    init_db()
    # ensure werkzeug.__version__ exists (some installs omit it)
    try:
        import werkzeug
        if not hasattr(werkzeug, '__version__'):
            werkzeug.__version__ = '3.0.0'
    except Exception:
        pass

    with app.test_client() as client:
        yield client


def test_index_empty(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b'No products yet' in response.data


def test_upload_product(client):
    response = client.post('/upload', data={
        'name': 'Test Product',
        'description': 'A sample product',
        'contact': 'test@example.com',
        'price': '9.99',
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b'Product uploaded successfully.' in response.data
    assert b'Test Product' in response.data
    assert b'A sample product' in response.data
    assert b'test@example.com' in response.data


def test_upload_invalid_price(client):
    response = client.post('/upload', data={
        'name': 'Bad Price',
        'description': 'Price invalid',
        'contact': 'bad@example.com',
        'price': 'not-a-number',
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b'Price must be a number.' in response.data


def test_product_detail_page(client):
    conn = sqlite3.connect(app_module.DB_PATH)
    conn.execute(
        'INSERT INTO products (name, description, contact, price, image) VALUES (?, ?, ?, ?, ?)',
        ('Detail Product', 'Detailed view', 'details@example.com', 5.50, None)
    )
    conn.commit()
    product_id = conn.execute('SELECT id FROM products WHERE name = ?', ('Detail Product',)).fetchone()[0]
    conn.close()

    response = client.get(f'/product/{product_id}')
    assert response.status_code == 200
    assert b'Detail Product' in response.data
    assert b'Detailed view' in response.data


def test_admin_can_add_payment_method(client):
    # First user becomes admin
    response = client.post('/signup', data={'email': 'admin@example.com', 'password': 'password'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'Account created' in response.data

    response = client.post('/login', data={'email': 'admin@example.com', 'password': 'password'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'Logged in successfully.' in response.data

    response = client.post('/admin/payments', data={'name': 'PayPal', 'details': 'Use paypal.me/admin', 'active': 'on'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'Payment method added.' in response.data
    assert b'PayPal' in response.data

    # Ensure payment option appears on product detail page
    conn = sqlite3.connect(app_module.DB_PATH)
    conn.execute(
        'INSERT INTO products (name, description, contact, price, image) VALUES (?, ?, ?, ?, ?)',
        ('Detail Product', 'Detailed view', 'details@example.com', 5.50, None)
    )
    conn.commit()
    product_id = conn.execute('SELECT id FROM products WHERE name = ?', ('Detail Product',)).fetchone()[0]
    conn.close()

    response = client.get(f'/product/{product_id}')
    assert response.status_code == 200
    assert b'Payment options' in response.data
    assert b'PayPal' in response.data
    assert b'paypal.me/admin' in response.data
