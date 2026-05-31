import sqlite3
import tempfile
import pytest
import app as app_module

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
    try:
        import werkzeug
        if not hasattr(werkzeug, '__version__'):
            werkzeug.__version__ = '3.0.0'
    except Exception:
        pass

    with app.test_client() as client:
        yield client


def test_user_chat_and_admin_reply(client):
    # user posts a message
    response = client.post('/chat', data={'name': 'Bob', 'email': 'bob@example.com', 'message': 'Hello admin'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'Message sent' in response.data

    # admin creates account and replies
    response = client.post('/signup', data={'email': 'admin2@example.com', 'password': 'pw'}, follow_redirects=True)
    response = client.post('/login', data={'email': 'admin2@example.com', 'password': 'pw'}, follow_redirects=True)
    assert b'Logged in successfully.' in response.data

    response = client.get('/admin/chats')
    assert response.status_code == 200
    assert b'bob@example.com' in response.data

    # admin reply
    response = client.post('/admin/chats/bob@example.com', data={'message': 'Hi Bob, we received your message'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'Reply sent' in response.data

    # original user can see the admin reply if they use same email
    response = client.post('/login', data={'email': 'bob@example.com', 'password': 'doesnotmatter'}, follow_redirects=True)
    # login should fail because bob isn't a user; instead just view chat as guest by setting session email not available in this test harness
    # check DB directly
    conn = sqlite3.connect(app_module.DB_PATH)
    msg = conn.execute('SELECT sender, message FROM messages WHERE email = ? ORDER BY created_at ASC', ('bob@example.com',)).fetchall()
    conn.close()
    assert any(m[0] == 'admin' for m in msg)
