"""Justdial transport: plain curl_cffi with browser-impersonated TLS, no
browser, no login. Egress: one sticky proxy exit per thread session
(config.justdial_proxy(), knob JUSTDIAL_EXIT); a retryable failure drops the
session so the retry rides a fresh exit.

Akamai Bot Manager fronts www.justdial.com. The PUBLIC category and
business pages (/<City>/<Cat>/nct-…, /…_BZDET) and the old wap JSON
(/api/india_api_read/…) answer every curl profile — warm or cold, desktop
or Android — with a 14-byte "<HTML>\\n</HTML>" stub, and every datacenter
exit gets a flat 403. The site is a Next.js app, and its DATA ROUTES are
not under that rule (research 2026-10-24, 8/8 fresh IN residential exits):

  * GET /_next/data/<buildId>/resultspage.json?city=&search=[&ncatid=nct-…]
        -> the listing page as HTML with a full __NEXT_DATA__
        (listData: 10 rows + the ordered list of up to 100 docids). A
        category name without ncatid 301s to /<City>/<Cat>/nct-<id> (we
        read the id off the Location and re-request); an unknown city
        301s to Mumbai (-> NotFound). "<text>-in-<Area>" scopes an area.
  * GET …/detailspage.json?city=Mumbai&search=x&docid=<docid>
        -> the business page (results.results, ~235 keys). An unknown
        docid 302s to a search page (-> NotFound). city is cosmetic.
  * GET …/detail-reviews.json?…&docid=&pageIndex=N -> 10 reviews/page
        in Justdial's "relevant" order (sort params are ignored here).
  * GET …/detail-photos.json?…&docid= -> the whole gallery by tab.
  * GET /DT-<code> -> 302 Location = the business page (short links).
  * GET https://auto.127777.com/auto.php?… -> category / business
        autosuggest JSON (Referer + Origin justdial.com).

The buildId is a date stamp (17102026 on 2026-10-24) that changes with
every deploy; a stale one answers 404 whose own __NEXT_DATA__ carries the
current id, so _data_route() re-reads it from the 404 body and retries.

Everything the pages do client-side goes through the site's SIGNED /api/*
POST routes (the Next.js API layer), reproduced from the bundles
(static.127777.com, chunks 5761 + resultspage, 2026-10-24):
  headers  requestTime   = urlencoded "YYYY-D-M h:mm:ss AM" (JS
                           toLocaleDateString().split("/").reverse())
           securityToken = urlencoded hex of each digit char of that
                           string XOR the XOR-fold of API_SALT
           jdpk          = hex of a random 32-byte AES-GCM key
  body     jdlt (listing only) = hex AES-GCM(key, API_IV, JSON
                           {cnm,nxt,pg,trm,catid,ct,ar})
  * POST /api/resultsPageListing?bids=<buildId>  page 2+ / sorted /
        filtered listings: page N sends exactly the 10-docid slice
        docids[(N-1)*10:N*10] as nextdocid (30 or 50 -> empty page).
  * POST /api/getratings  reviews in latest / high-to-low order.
  * POST /api/callallocate  the "Show Number" virtual number of a free
        listing (paid listings print their number in the page).
These POST routes are a per-exit lottery (Akamai scores the XHR path by
exit reputation: ~1 in 3 fresh IN residential exits answers, a refused
exit stubs everything, an accepted exit then serves dozens of calls), so
the stub is Blocked and the retry ladder rotates the exit.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  JustdialUpstreamError  transport failure / 5xx / odd payload  — retryable
  JustdialBlocked        14-byte stub, 403 / 429                 — retryable
  JustdialBadRequest     upstream 400                           — never retried
  JustdialNotFound       unknown docid / city / short link      — never retried
"""
import datetime
import json
import os
import re
import secrets
import sys
import threading
import time
from urllib.parse import quote, urlencode, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from justdial.refs import SITE, normalize_docid

IMPERSONATE = "chrome"
PAGE_TIMEOUT = 45      # data routes are 300-600 KB of HTML
API_TIMEOUT = 30

SUGGEST_HOST = "https://auto.127777.com"

# From the site's bundle (chunk 5761, module 54728): the salt the API
# token is XOR'd with; and (resultspage chunk, module 27518) the fixed
# AES-GCM IV the listing cursor is encrypted with. Captured 2026-10-24.
API_SALT = "Ge8V*WqBKzv#6wTj3!9KgDsHS?h?B"
API_IV = bytes([54, 61, 189, 63, 251, 125, 69, 229, 213, 168, 92, 12])

STUB_BODIES = {"<HTML>\n</HTML>", "<HTML></HTML>", "<HTML>\r\n</HTML>"}

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_LOCATION_NCT_RE = re.compile(r"^/(?:[a-z]{2}/)?([^/]+)/([^/?#]+)/nct-([0-9]+)", re.I)
_LOCATION_CITY_RE = re.compile(r"^/(?:[a-z]{2}/)?([^/?#]+)", re.I)


class JustdialUpstreamError(UpstreamError):
    """Transport failure, 5xx or an unexpected payload — retryable."""


class JustdialBlocked(JustdialUpstreamError, Blocked):
    """Akamai's 14-byte stub, 403 / 429 — retryable on a fresh exit."""


class JustdialBadRequest(BadRequest):
    """Upstream 400 — never retried."""


class JustdialNotFound(NotFound):
    """Unknown docid / city / short link — never retried."""


# ---- sessions ------------------------------------------------------------------

_local = threading.local()


def _session():
    sess = getattr(_local, "session", None)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = config.justdial_proxy()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        sess.headers["accept-language"] = egress.accept_language(proxy)
        _local.session = sess
    return sess


def _drop_session():
    """Close the thread's session: a refused exit must not be reused."""
    sess = getattr(_local, "session", None)
    _local.session = None
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def _retrying(fn, attempts=None):
    """Run fn() under the shared retry policy (JustdialUpstreamError only;
    NotFound / BadRequest surface immediately). Each retry drops the
    session, so it leaves through a fresh sticky exit. The signed /api/*
    POSTs pass `attempts` = config.JUSTDIAL_API_EXIT_ATTEMPTS: their stub
    is an instant per-exit verdict, so more exits are tried."""
    attempts = attempts or config.MAX_RETRIES
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except JustdialUpstreamError as e:
            last = e
            _drop_session()
            if attempt < attempts:
                time.sleep(config.RETRY_BACKOFF * min(attempt, 2))
    raise last


def _fresh(sess):
    """The jar is emptied before every request: the site writes the last
    searched area / city into cookies (pageConfig.rewriteLocationCookies)
    and would scope the next search to them; Akamai's cookies never help."""
    sess.cookies.clear()
    return sess


PAGE_HEADERS = {
    "accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "upgrade-insecure-requests": "1",
}


def _is_stub(text):
    return (text or "").strip() in STUB_BODIES


def _classify(resp, label):
    """HTTP status -> error taxonomy; returns the body text when it is a
    normal answer (200 or a redirect the caller inspects)."""
    if resp.status_code == 400:
        raise JustdialBadRequest(resp.text[:200])
    if resp.status_code in (401, 403, 429):
        raise JustdialBlocked(f"HTTP {resp.status_code} for {label}")
    if resp.status_code >= 500:
        raise JustdialUpstreamError(f"HTTP {resp.status_code} for {label}")
    text = resp.text
    if resp.status_code == 200 and _is_stub(text):
        raise JustdialBlocked(f"Akamai stub for {label}")
    return text


def next_data(html):
    """The __NEXT_DATA__ JSON of a server-rendered page, or None."""
    match = _NEXT_DATA_RE.search(html or "")
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


# ---- the Next.js build id ---------------------------------------------------------

_build = {"id": config.JUSTDIAL_BUILD_ID_SEED, "lock": threading.Lock()}


def build_id():
    with _build["lock"]:
        return _build["id"]


def _learn_build_id(html):
    """A 404 from a stale data route is the site's own 404 page, whose
    __NEXT_DATA__ names the live buildId."""
    data = next_data(html) or {}
    new = data.get("buildId")
    if new and re.fullmatch(r"[A-Za-z0-9_-]{4,64}", str(new)):
        with _build["lock"]:
            changed = new != _build["id"]
            _build["id"] = new
        return changed
    return False


# ---- the data routes -----------------------------------------------------------------

class DataRoute:
    """One answer from a /_next/data route: the __NEXT_DATA__ dict, or the
    Location of a redirect (never followed: the public paths are stubbed)."""

    def __init__(self, data=None, location=None):
        self.data = data
        self.location = location

    @property
    def page_props(self):
        return ((self.data or {}).get("props") or {}).get("pageProps") or {}


def _data_route(page, params, label):
    """GET /_next/data/<buildId>/<page>.json?<params> -> DataRoute."""
    def once():
        for stale_retry in (False, True):
            url = f"{SITE}/_next/data/{build_id()}/{page}.json?{urlencode(params)}"
            try:
                resp = _fresh(_session()).get(url, headers=PAGE_HEADERS, timeout=PAGE_TIMEOUT,
                                              allow_redirects=False)
            except Exception as e:
                raise JustdialUpstreamError(f"request failed: {type(e).__name__}: {e}")
            if resp.status_code == 404:
                if not stale_retry and _learn_build_id(resp.text):
                    continue      # the deploy moved; retry on the live buildId
                raise JustdialNotFound(f"{label} not found")
            text = _classify(resp, label)
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location") or ""
                if not location:
                    raise JustdialUpstreamError(f"redirect without Location for {label}")
                return DataRoute(location=location)
            if resp.status_code != 200:
                raise JustdialUpstreamError(f"HTTP {resp.status_code} for {label}")
            data = next_data(text)
            if not data:
                raise JustdialBlocked(f"no __NEXT_DATA__ for {label}: {text[:80]!r}")
            return DataRoute(data=data)
    return _retrying(once)


def _path_of(location):
    return urlparse(location).path if location.startswith(("http://", "https://")) else location.split("?")[0]


def _same_city(a, b):
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
    return norm(a) == norm(b)


def fetch_results(city, search, ncatid=None):
    """The listing page's pageProps for a city + search slug
    ("Restaurants", "Restaurants-in-Andheri-West", "bademiya"). Without a
    category id the site 301s a category name to its nct-… page: the id is
    read off the Location and the route re-requested with it. An unknown
    city 301s to Mumbai -> NotFound."""
    params = {"city": city, "search": search}
    if ncatid:
        params["ncatid"] = f"nct-{ncatid}"
    label = f"results {city}/{search}"
    answer = _data_route("resultspage", params, label)
    if answer.location is None:
        return answer.page_props
    path = _path_of(answer.location)
    city_match = _LOCATION_CITY_RE.match(path)
    redirect_city = city_match.group(1) if city_match else ""
    if not _same_city(redirect_city, city) and _same_city(redirect_city, "Mumbai"):
        raise JustdialNotFound(f"Justdial does not list a city called {city!r}")
    nct = _LOCATION_NCT_RE.match(path)
    if nct and not ncatid:
        return fetch_results(city, nct.group(2), nct.group(3))
    raise JustdialUpstreamError(f"unexpected redirect for {label}: {answer.location[:120]}")


def fetch_detail(docid):
    """The business page's pageProps (results.results carries the record).
    An unknown docid is a 302 to a search page -> NotFound."""
    docid = normalize_docid(docid)
    answer = _data_route("detailspage", {"city": "Mumbai", "search": "x", "docid": docid},
                         f"business {docid}")
    if answer.location is not None:
        raise JustdialNotFound(f"business {docid} not found")
    props = answer.page_props
    if not ((props.get("results") or {}).get("results") or {}).get("name"):
        raise JustdialNotFound(f"business {docid} not found")
    return props


def fetch_reviews_page(docid, page=1):
    """The reviews tab's pageProps (reviews.data.rating = 10 reviews in the
    site's "relevant" order; reviews.data.count = the total)."""
    docid = normalize_docid(docid)
    params = {"city": "Mumbai", "search": "x", "docid": docid}
    if page > 1:
        params["pageIndex"] = page
    answer = _data_route("detail-reviews", params, f"reviews {docid} p{page}")
    if answer.location is not None:
        raise JustdialNotFound(f"business {docid} not found")
    props = answer.page_props
    if not isinstance(props.get("reviews"), dict):
        raise JustdialNotFound(f"business {docid} not found")
    return props


def fetch_photos(docid):
    """The photos tab's pageProps (gallery: every photo by tab + videos)."""
    docid = normalize_docid(docid)
    answer = _data_route("detail-photos", {"city": "Mumbai", "search": "x", "docid": docid},
                         f"photos {docid}")
    if answer.location is not None:
        raise JustdialNotFound(f"business {docid} not found")
    props = answer.page_props
    if not isinstance(props.get("gallery"), dict):
        raise JustdialNotFound(f"business {docid} not found")
    return props


def resolve_short_link(code):
    """DT-<code> -> the docid its 302 Location points at."""
    code = code[3:] if code.upper().startswith("DT-") else code

    def once():
        try:
            resp = _fresh(_session()).get(f"{SITE}/DT-{code}", headers=PAGE_HEADERS,
                                          timeout=API_TIMEOUT, allow_redirects=False)
        except Exception as e:
            raise JustdialUpstreamError(f"request failed: {type(e).__name__}: {e}")
        if resp.status_code == 404:
            raise JustdialNotFound(f"short link DT-{code} not found")
        _classify(resp, f"short link DT-{code}")
        location = resp.headers.get("location") or ""
        match = re.search(r"/([0-9]{2,4}P[A-Za-z0-9]+(?:-[A-Za-z0-9]+){0,3})_BZDET", location)
        if not match:
            if resp.status_code in (301, 302) or resp.status_code == 200:
                raise JustdialNotFound(f"short link DT-{code} not found")
            raise JustdialUpstreamError(f"HTTP {resp.status_code} for short link DT-{code}")
        return normalize_docid(match.group(1))
    return _retrying(once)


# ---- autosuggest -----------------------------------------------------------------------

SUGGEST_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": SITE,
    "referer": SITE + "/",
}


def fetch_suggest(term, city):
    """auto.127777.com autosuggest -> the raw results list (categories and
    businesses matching `term` in `city`)."""
    params = {"source": 77, "wap": 77, "version": "2.9", "user": 1, "search": term,
              "deviceId": "", "city": city, "dtcity": city}

    def once():
        try:
            resp = _fresh(_session()).get(f"{SUGGEST_HOST}/auto.php?{urlencode(params)}",
                                          headers=SUGGEST_HEADERS, timeout=API_TIMEOUT)
        except Exception as e:
            raise JustdialUpstreamError(f"request failed: {type(e).__name__}: {e}")
        text = _classify(resp, "suggest")
        try:
            payload = json.loads(text.strip() or "{}")
        except ValueError:
            raise JustdialBlocked(f"non-JSON suggest response: {text[:100]!r}")
        if not isinstance(payload, dict):
            raise JustdialUpstreamError("unexpected suggest payload")
        results = payload.get("results")
        return results if isinstance(results, list) else []
    return _retrying(once)


# ---- the signed /api/* routes ----------------------------------------------------------

def _xor_fold(text):
    value = 0
    for ch in text:
        value ^= ord(ch)
    return value


def security_headers(now=None):
    """requestTime + securityToken as the site's axios interceptor sends
    them (chunk 5761: getApiSecurityInfo). The clock is the browser's local
    time; our exits are Indian, so IST."""
    now = now or (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30))
    hour12 = now.hour % 12 or 12
    stamp = (f"{now.year}-{now.day}-{now.month} {hour12}:{now.minute:02d}:{now.second:02d} "
             f"{'PM' if now.hour >= 12 else 'AM'}")
    digits = re.sub(r"[^0-9]+", "", stamp)
    key = _xor_fold(API_SALT)
    token = "".join(f"{(ord(c) ^ key) & 0xff:02x}" for c in digits)
    return {"requestTime": quote(stamp), "securityToken": quote(token)}


def _api_key():
    """One random AES-GCM key per thread session (the browser keeps one per
    tab): hex goes up as the jdpk header, the bytes encrypt jdlt."""
    key = getattr(_local, "api_key", None)
    if key is None or getattr(_local, "api_key_session", None) is not _session():
        key = secrets.token_bytes(32)
        _local.api_key = key
        _local.api_key_session = _session()
    return key


def encrypt_cursor(payload, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return AESGCM(key).encrypt(API_IV, data, None).hex()


API_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "origin": SITE,
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def _api_post(path, body, referer, label, cursor=None):
    """POST a signed /api/* route; returns the parsed JSON object."""
    def once():
        sess = _fresh(_session())
        key = _api_key()
        payload = dict(body)
        if cursor is not None:
            payload["jdlt"] = encrypt_cursor(cursor, key)
        sep = "&" if "?" in path else "?"
        headers = dict(API_HEADERS, referer=referer, jdpk=key.hex(), **security_headers())
        try:
            resp = sess.post(f"{SITE}{path}{sep}searchReferer=gen", json=payload,
                             headers=headers, timeout=API_TIMEOUT)
        except Exception as e:
            raise JustdialUpstreamError(f"request failed: {type(e).__name__}: {e}")
        text = _classify(resp, label)
        try:
            data = json.loads(text)
        except ValueError:
            raise JustdialBlocked(f"non-JSON answer for {label}: {text[:80]!r}")
        if not isinstance(data, dict):
            raise JustdialUpstreamError(f"unexpected payload for {label}")
        if data.get("error") and data.get("message"):
            # {"error":1,"message":"Invalid request"} = the signature was refused
            raise JustdialUpstreamError(f"{label}: {data.get('message')}")
        return data
    return _retrying(once, attempts=config.JUSTDIAL_API_EXIT_ATTEMPTS)


def _listing_body(city, search, ncatid, area, page, docids, search_id, attributes, stype):
    return {
        "median_latitude": "", "median_longitude": "",
        "city": city, "search": search, "area": area or "", "lat": "", "long": "",
        "national_catid": ncatid or "", "nextdocid": docids, "stype": stype, "opt": "",
        "login_mobile": "", "pg_no": page, "nearme": "0",
        "attribute_values": quote(json.dumps(attributes, separators=(",", ":")), safe="") if attributes else "",
        "mncatname": search if ncatid else "", "darea_flg": 1 if area else 0,
        "bid": 0, "bizId": "", "pdid": [], "trkid": "", "version": "", "search_id": search_id or "",
        "attr": "", "chainotlt": "", "asnm": 0, "cbflg": "", "selectedImage": "", "isgroup": "",
    }


def fetch_listing_api(city, search, ncatid=None, area=None, page=1, docids="", search_id="",
                      attributes=None):
    """POST /api/resultsPageListing: page 1 (docids="") returns 10 rows +
    the full ordered docid list; page N returns the rows of the 10 docids
    passed as `docids`. `attributes` = the site's filter list
    ([{"e":"100","v":["Rating"]}, {"e":"6","v":["HR"]}, {"e":"10","v":["OPEN"]}])."""
    stype = "category_list" if ncatid else "company_list"
    body = _listing_body(city, search, ncatid, area, page, docids, search_id, attributes, stype)
    cursor = {"cnm": search if ncatid else "", "nxt": docids, "pg": page, "trm": search,
              "catid": ncatid or "", "ct": city, "ar": area or ""}
    referer = f"{SITE}/{quote(city)}/{quote(search)}" + (f"/nct-{ncatid}" if ncatid else "")
    data = _api_post(f"/api/resultsPageListing?bids={build_id()}", body, referer,
                     f"listing api {city}/{search} p{page}", cursor=cursor,)
    if not isinstance(data.get("results"), (dict, list)):
        raise JustdialUpstreamError("listing api: no results block")
    return data


def fetch_ratings_api(docid, sort="re", page=1):
    """POST /api/getratings: reviews in `sort` order (po relevant, re latest,
    hl high-to-low), 10 per page."""
    docid = normalize_docid(docid)
    body = {"city": "Mumbai", "docid": docid, "gdocids": docid, "smode": sort, "np": page,
            "mobile": "", "case": "", "apiversion": 1.4, "popTags": True, "jdrating": "",
            "jdreview": "", "ishotel": False}
    referer = f"{SITE}/Mumbai/x/{docid.replace('.', '-')}_BZDET/reviews"
    data = _api_post("/api/getratings", body, referer, f"ratings api {docid} p{page}")
    if not isinstance(data.get("data"), dict):
        raise JustdialUpstreamError("ratings api: no data block")
    return data


def fetch_call_allocate(allocate_id, ncatid=None):
    """POST /api/callallocate: the number Justdial reveals on "Show Number"
    for a listing whose page prints none (result.vn)."""
    body = {"allocateid": allocate_id, "mobile": "", "ask_mob": 0, "is_verified": 0,
            "ncatid": ncatid or "", "page": "results"}
    data = _api_post("/api/callallocate", body, SITE + "/", "call allocate")
    result = data.get("result")
    if not isinstance(result, dict):
        raise JustdialUpstreamError("call allocate: no result block")
    return result
