# worker.py
import os
from redis import Redis
from rq import Worker, Queue, Connection

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
listen = ["quiz-jobs"]

redis_conn = Redis.from_url(REDIS_URL)

if __name__ == "__main__":
    with Connection(redis_conn):
        w = Worker(list(map(Queue, listen)))
        w.work()
