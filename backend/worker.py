"""Run the analysis worker separately when INLINE_WORKER=false."""

from threading import Event
import signal

from account_v2 import close_database
from analysis_v2 import run_worker


if __name__ == "__main__":
    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        run_worker(stop)
    finally:
        close_database()
