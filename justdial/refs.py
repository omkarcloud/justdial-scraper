"""Reference resolvers + link builders for /justdial/*.

ONE param per input (tripadvisor QueryOrIdField convention): `company`
accepts a bare Justdial business id (docid) OR any justdial.com business
link OR a DT-… short link; `query` accepts a category name / free text OR
a justdial.com category link (which carries the city, the area and the
category id). Resolvers raise ValueError with a user-facing message
(RefField turns it into a 400).

Identifier shapes (captured 2026-10-24):
  * docid   the business id. Dotted in the site's JSON
            (022PXX22.XX22.140804130028.J6U9, 022P9830068, 022PJD00936,
            022P1233566567F2V6U1); dashed in links with a _BZDET suffix
            (…/022PXX22-XX22-140804130028-J6U9_BZDET). The 3-digit prefix
            is the city's STD code. Canonical form here: dotted, upper-case.
  * short   https://www.justdial.com/DT-43RCW5QC (also jsdl.in/DT-…): a
            302 to the business page — resolved over the network by
            fetch.resolve_short_link, so the resolver hands back "DT-<code>".
  * ncatid  the national category id, nct-10408936 in links
            (/Mumbai/Restaurants/nct-10408936,
             /Mumbai/Restaurants-in-Andheri-West/nct-10408936/page-2).
"""
import re
from urllib.parse import unquote, urlparse

SITE = "https://www.justdial.com"
SHORT_HOSTS = ("justdial.com", "jsdl.in")

DOCID_RE = re.compile(r"^[0-9]{2,4}P[A-Z0-9]+(?:[.-][A-Z0-9]+){0,3}$", re.I)
SHORT_CODE_RE = re.compile(r"^DT-([A-Z0-9]{4,20})$", re.I)
NCT_RE = re.compile(r"^(?:nct-)?([0-9]{4,12})$", re.I)

_LINK_DOCID_RE = re.compile(r"/([0-9]{2,4}P[A-Za-z0-9]+(?:-[A-Za-z0-9]+){0,3})_BZDET(?:[/?#]|$)")
_LINK_SHORT_RE = re.compile(r"/(DT-[A-Za-z0-9]{4,20})(?:[/?#]|$)")
_LINK_NCT_RE = re.compile(r"^/(?:[a-z]{2}/)?([^/]+)/([^/]+)/nct-([0-9]+)", re.I)
_AREA_RE = re.compile(r"^(.*?)-in-(.+)$", re.I)


def _host_is(value, domains):
    host = (urlparse(value).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def _as_url(value):
    return value if not value.startswith("//") else "https:" + value


def normalize_docid(value):
    """Dashed or dotted docid -> dotted upper-case (None / "" -> None)."""
    value = (value or "").strip().upper().replace("-", ".")
    return value or None


def resolve_company(value):
    """Bare docid, a justdial.com business link or a DT short link -> the
    dotted docid, or "DT-<code>" for a short link (resolved by fetch)."""
    value = (value or "").strip()
    if DOCID_RE.match(value):
        return normalize_docid(value)
    if SHORT_CODE_RE.match(value):
        return "DT-" + value.split("-", 1)[1].upper()
    if value.startswith(("http://", "https://", "//")):
        url = _as_url(value)
        if not _host_is(url, SHORT_HOSTS):
            raise ValueError("Must be a justdial.com link.")
        path = urlparse(url).path
        match = _LINK_DOCID_RE.search(path)
        if match:
            return normalize_docid(match.group(1))
        match = _LINK_SHORT_RE.search(path)
        if match:
            return "DT-" + match.group(1).split("-", 1)[1].upper()
        raise ValueError("Must be a justdial.com business link "
                         "(https://www.justdial.com/<City>/<name>/<id>_BZDET) "
                         "or a DT-… short link.")
    raise ValueError("Must be a Justdial business id (022PXX22.XX22.140804130028.J6U9), "
                     "a justdial.com business link or a DT-… short link.")


def is_short_code(ref):
    return isinstance(ref, str) and ref.startswith("DT-")


def resolve_query(value):
    """A category name / free text, a bare category id (nct-10408936) or a
    justdial.com category link -> {"text", "ncatid", "city", "area"}.
    city / area are None unless the link carries them."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Must not be empty.")
    match = NCT_RE.match(value)
    if match and value.lower().startswith("nct-"):
        return {"text": None, "ncatid": match.group(1), "city": None, "area": None}
    if value.startswith(("http://", "https://", "//")):
        url = _as_url(value)
        if not _host_is(url, ("justdial.com",)):
            raise ValueError("Must be a justdial.com link.")
        path = unquote(urlparse(url).path)
        match = _LINK_NCT_RE.match(path)
        if not match:
            raise ValueError("Must be a justdial.com category link "
                             "(https://www.justdial.com/<City>/<Category>/nct-<id>).")
        city, slug, ncatid = match.group(1), match.group(2), match.group(3)
        text, area = slug, None
        area_match = _AREA_RE.match(slug)
        if area_match:
            text, area = area_match.group(1), area_match.group(2)
        return {"text": unslug(text), "ncatid": ncatid, "city": unslug(city), "area": unslug(area)}
    return {"text": value, "ncatid": None, "city": None, "area": None}


# ---- slugs / links -------------------------------------------------------------

def slug(text):
    """The site's own path slug: drop odd characters, spaces -> '-'."""
    text = re.sub(r"[^A-Za-z0-9\-\s.]+", "", text or "")
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"\.+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


def unslug(text):
    return " ".join(text.replace("-", " ").split()) if text else None


def search_slug(text, area=None):
    """The `search` path segment: "<text>" or "<text>-in-<area>"."""
    return slug(text) + (f"-in-{slug(area)}" if area else "")


def company_link(docid, city=None, name=None, path=None):
    """The canonical business page. `path` is the site's own weburl
    ("Mumbai/Name-Area/022PXX22-…_BZDET") when a payload carries it."""
    if path:
        return f"{SITE}/{path.lstrip('/')}"
    dashed = normalize_docid(docid).replace(".", "-")
    return f"{SITE}/{slug(city or 'Mumbai')}/{slug(name or 'business') or 'business'}/{dashed}_BZDET"


def short_link(code):
    """https://www.justdial.com/DT-<code> for a sot_code / sharedt code."""
    code = (code or "").strip()
    if not code:
        return None
    if code.upper().startswith("DT-"):
        code = code[3:]
    return f"{SITE}/DT-{code}"


def category_link(city, name, ncatid, area=None):
    if not (city and name and ncatid):
        return None
    return f"{SITE}/{slug(city)}/{search_slug(name, area)}/nct-{ncatid}"
