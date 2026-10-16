"""Run `python worker.py` beside Gunicorn, with the same DB and SECRET_KEY."""
import logging
import signal
import time
from app import app as application
import engine

running=True

def stop(*_):
    global running
    running=False


def main():
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s worker %(levelname)s %(message)s')
    with application.app_context():
        while running:
            try:engine.step(application.config['DATABASE_PATH'])
            except Exception:logging.exception('Tick rolled back; retrying without losing progress')
            time.sleep(1)

if __name__=='__main__':main()
