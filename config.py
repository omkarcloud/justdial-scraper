"""Configuration for the Justdial Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT            port the API listens on (default 8000)
    JUSTDIAL_PROXY  proxy URL for every request, e.g. http://user:pass@host:port
                    (default: none — direct). Justdial answers home and office
                    connections directly, in India and abroad, but refuses
                    cloud / datacenter server IPs (AWS, Google Cloud, ...)
                    with a blank page. Running on a cloud server? Set this to
                    a residential proxy.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

# Justdial's signed listing / review / phone calls refuse a share of requests
# at random with a blank page; these calls get a longer retry ladder.
JUSTDIAL_API_EXIT_ATTEMPTS = 6

# The site's Next.js build id (a date stamp). Only the first request uses it:
# when Justdial deploys, the scraper reads the new id from the answer itself.
JUSTDIAL_BUILD_ID_SEED = "17102026"

JUSTDIAL_PROXY = os.environ.get("JUSTDIAL_PROXY") or None


def justdial_proxy():
    """The proxy for one curl session (None = direct)."""
    return os.environ.get("JUSTDIAL_PROXY") or None
