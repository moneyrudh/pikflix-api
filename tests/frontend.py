"""Headers the frontend's proxy routes send; the backend refuses /api requests without the secret."""
import os

FRONTEND = {"X-Pikflix-Proxy-Secret": os.environ["BACKEND_SHARED_SECRET"], "X-Pikflix-Client-Ip": "203.0.113.7"}
