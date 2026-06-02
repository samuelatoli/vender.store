from app import app, socketio, init_db


def main():
    init_db()
    if socketio:
        # Use Socket.IO server when available (eventlet/gevent recommended)
        socketio.run(app, host='0.0.0.0', port=5000, debug=True)
    else:
        app.run(host='0.0.0.0', port=5000, debug=True)


if __name__ == '__main__':
    main()
