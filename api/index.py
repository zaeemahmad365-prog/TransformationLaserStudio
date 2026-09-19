"""Vercel invokes this handler; importing server never starts a local listener."""
from server import Handler


class handler(Handler):
    pass
