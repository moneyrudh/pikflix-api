from slowapi import Limiter
from slowapi.util import get_remote_address

# Rate limiter keyed by client IP address.
# Default: 30 requests/minute — protects the Anthropic API from abuse.
limiter = Limiter(key_func=get_remote_address, default_limits=["30/minute"])
