"""Raw Justdial payloads -> clean dicts for every /justdial/* endpoint
(pure functions, no I/O).

Inputs are the pageProps of the site's Next.js data routes (fetch.py:
resultspage / detailspage / detail-reviews / detail-photos) and the JSON of
its signed /api/* routes (resultsPageListing, getratings, callallocate) and
the auto.127777.com autosuggest. The listing rows are COLUMNAR
(results.columns + results.data, 88 columns) and are zipped here.

Value conventions (shared with the other scraper packages):
  * keys     snake_case; `link` not `url`; booleans as questions
  * masks    "" / "xxxxxxx" (a masked WhatsApp number) / "0000-00-00" -> null
  * numbers  "24,828" -> 24828; "4.2" -> 4.2
  * dates    review timestamps (ms) -> ISO 8601 UTC; "22nd October, 2026"
             -> "2026-10-22"; "Last Date: 30 Apr 2027" -> "2027-04-30"
  * html     the status snippets ("<span …>Open</span> until 1:00 am") are
             flattened to text

Fixtures captured 2026-10-26 (justdial/fixtures/): a category listing
(Mumbai restaurants, page 1), an area-scoped listing, a free-text search,
a small-city listing, a paid restaurant page (every section filled), a
free listing (sparse: no printed number, no services), a doctor page, two
review pages + an empty one, two galleries, three autosuggest answers, a
sorted listing-API page 1, a listing-API page 2, a ratings-API page and a
callallocate answer.
"""
import json
import re
from datetime import datetime, timezone

from justdial.refs import SITE, category_link, company_link, normalize_docid, short_link

_MASKS = {"", "-", "N/A", "NA", "null", "None", "0000-00-00", "1970-01-01"}
_TAG_RE = re.compile(r"<[^>]+>")
_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")
_LONG_DATE_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(\d{4})")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ---- value helpers -------------------------------------------------------------

def _clean(value):
    """Collapse whitespace, drop HTML tags; masks -> None."""
    if value is None or isinstance(value, (dict, list)):
        return None
    text = " ".join(_TAG_RE.sub(" ", str(value)).split())
    if text in _MASKS or re.fullmatch(r"x{4,}", text, re.I):
        return None
    return text


def _int(value):
    text = _clean(value)
    if text is None:
        return None
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    try:
        return int(float(match.group(0).replace(",", "")))
    except ValueError:
        return None


def _float(value):
    text = _clean(value)
    if text is None:
        return None
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _flag(value):
    """"1" / 1 / True -> True; "0" / "" / None -> False."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def _split(value, sep=","):
    if isinstance(value, list):
        items = [_clean(v) for v in value]
    else:
        text = _clean(value)
        items = [v.strip() for v in text.split(sep)] if text else []
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _json(value, default=None):
    """A JSON string embedded in a string field -> the object."""
    if isinstance(value, (dict, list)):
        return value
    if not value or not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


def _abs(link):
    link = _clean(link)
    if not link:
        return None
    if link.startswith("//"):
        return "https:" + link
    if link.startswith("/"):
        return SITE + link
    if not link.startswith(("http://", "https://")):
        return SITE + "/" + link
    return link


def _rating(value):
    rating = _float(value)
    return rating if rating is not None and 0 <= rating <= 5 else None


def iso_from_ms(value):
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    if ms < 10 ** 11:          # seconds, not ms
        ms *= 1000
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_long_date(value):
    """"22nd October, 2026" / "Last Date: 30 Apr 2027" -> "2026-10-22"."""
    text = _clean(value)
    if not text:
        return None
    match = _LONG_DATE_RE.search(text)
    if not match:
        return None
    day, month, year = match.groups()
    month_no = _MONTHS.get(month[:3].lower())
    if not month_no:
        return None
    return f"{int(year):04d}-{month_no:02d}-{int(day):02d}"


def parse_datetime(value):
    """"2020-07-28 20:09:12" -> "2020-07-28T20:09:12" (site-local IST)."""
    text = _clean(value)
    if not text:
        return None
    match = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", text)
    if match:
        return f"{match.group(1)}T{match.group(2)}"
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _inr(value):
    """"₹ 650 Price for two" / "Price For Two: ₹ 650" -> {"amount": 650, "currency": "INR"}."""
    amount = _int(value)
    return {"amount": amount, "currency": "INR"} if amount is not None else None


def _price_band(value):
    """"0-500" -> {"min": 0, "max": 500, "currency": "INR"}."""
    text = _clean(value)
    if not text or not re.fullmatch(r"\d+\s*-\s*\d+", text):
        return None
    low, high = (int(v) for v in re.split(r"\s*-\s*", text))
    return {"min": low, "max": high, "currency": "INR"}


def _years_in_business(node):
    """attr_data.node3 = ["12 Years in Business", 1, 1]."""
    if isinstance(node, list) and node:
        text = _clean(node[0])
        if text and "year" in text.lower():
            return _int(text)
    return None


def _status(value):
    """"<span>Open</span> <span> until 1:00 am</span>" -> "Open until 1:00 am"."""
    return _clean(value)


# ---- listing rows ----------------------------------------------------------------

def _rows(results):
    """results {columns, data} (columnar) or a list of dicts -> list of dicts."""
    if isinstance(results, list):
        return [r for r in results if isinstance(r, dict)]
    if not isinstance(results, dict):
        return []
    columns = results.get("columns") or []
    data = results.get("data") or []
    out = []
    for row in data:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, list) and columns:
            out.append(dict(zip(columns, row)))
    return out


def _category_from(name, nid, city=None, area=None, link=None):
    name = _clean(name)
    nid = _clean(nid)
    if not (name or nid):
        return None
    return {"name": name, "id": nid, "link": _abs(link) if link else category_link(city, name, nid, area)}


def parse_listing_row(row):
    """One listing row (88 columns) -> the company summary."""
    event = row.get("event_data") if isinstance(row.get("event_data"), dict) else {}
    hcat = row.get("hcatarr") if isinstance(row.get("hcatarr"), dict) else {}
    attr = row.get("attr_data") if isinstance(row.get("attr_data"), dict) else {}
    opstring = row.get("opstring") if isinstance(row.get("opstring"), dict) else {}
    docid = normalize_docid(_clean(row.get("docid")) or "") or None
    # `city` is the SEARCHED city (lower-cased on some rows); `loccity` /
    # event_data.city is where the business actually is (a Mumbai search
    # pads with Thane / Navi Mumbai rows).
    city = _clean(row.get("loccity")) or _clean(event.get("city")) or _clean(row.get("city"))
    if city:
        city = city if city[:1].isupper() else city.title()
    paid = _clean(row.get("paidStatus"))
    whatsapp = next((w for w in _split(row.get("wpnumber")) if w), None)
    return {
        "id": docid,
        "name": _clean(row.get("name")),
        "link": company_link(docid, city, row.get("name"), path=_clean(row.get("weburl"))) if docid else None,
        "short_link": _abs(row.get("sharedt_url")) if _clean(row.get("sharedt_url")) else None,
        "category": _category_from(hcat.get("name"), hcat.get("nid") or hcat.get("id"), city),
        "categories": _split(row.get("catarray")),
        "business_type": _clean(row.get("vertical")) or None,
        "rating": _rating(row.get("compRating")),
        "ratings_count": _int(row.get("totJdReviews")),
        "is_verified": _flag(row.get("verified")) or _flag(event.get("verified")),
        "is_jd_verified": _flag(event.get("jd_verified")),
        "is_jd_trusted": _flag(event.get("trust")),
        "is_paid_listing": bool(paid) and paid != "0",
        "is_ad": _flag(row.get("ad_listing")),
        "phone": _clean(row.get("VNumber")),
        "whatsapp": whatsapp,
        "address": {
            "text": _clean(row.get("NewAddress")),
            "area": _clean(row.get("area")),
            "city": city,
            "pincode": _clean(row.get("pincode")),
            "latitude": _float(row.get("lat")),
            "longitude": _float(row.get("lon")),
        },
        "hours": {
            "status": _status(opstring.get("status")),
            "today": _clean(opstring.get("timing")),
            "is_open_now": _flag(row.get("opennow")),
        },
        "price_for_two": _inr(attr.get("node2")),
        "years_in_business": _years_in_business(attr.get("node3")),
        "highlight": _clean(attr.get("node1")),
        "thumbnail": _abs(row.get("thumbnail")),
        "logo": _abs(row.get("logo")) if _clean(row.get("logo")) else None,
        "images": [_abs(i) for i in (row.get("dimages") or []) if _clean(i)] if isinstance(row.get("dimages"), list) else [],
        "photos_count": _int(row.get("photocnt")),
        "distance_km": _float(row.get("distance")),
        "position": _int(row.get("idxno")) if _int(row.get("idxno")) else _int(event.get("pos")),
        "tagline": _clean(row.get("nwtaglin")) or _clean(row.get("tag")) or None,
    }


def _listing_meta(payload):
    """The listing block's own description of the search (both the SSR
    listData and the listing API carry the same keys)."""
    seo = payload.get("seo_dt") if isinstance(payload.get("seo_dt"), dict) else {}
    sorting = payload.get("sortingOption") if isinstance(payload.get("sortingOption"), dict) else {}
    free_text = _flag(payload.get("isFreeTxt")) or _flag(seo.get("isfreetext"))
    keyword = _clean(payload.get("keyword"))
    # seo_dt.hot_cat_arr is the FIRST ROW's category, not the searched one:
    # the search's own category is keyword + national_catid (+ seo_dt.link).
    category = None if free_text else _category_from(keyword, payload.get("national_catid"), link=seo.get("link"))
    return {
        "keyword": keyword,
        "title": _clean(payload.get("h1")) or _clean(seo.get("title")),
        "is_free_text": free_text,
        "category_id": _clean(payload.get("national_catid")),
        "matched_category": category,
        "sort_options": [s for s in (sorting.get("basedon") or []) if isinstance(s, str)],
        "total": _int(payload.get("totalNumberofResults")),
        "docids": [d for d in _split(payload.get("nextdocid")) if d],
        "search_id": _clean(payload.get("search_id")),
        "median_latitude": _clean(payload.get("median_latitude")),
        "median_longitude": _clean(payload.get("median_longitude")),
    }


def parse_search(page_props):
    """resultspage.json pageProps -> {"city", "area", "query", "category",
    "total_count", "docids", "search_id", "results"}."""
    props = page_props or {}
    listing = props.get("listData") if isinstance(props.get("listData"), dict) else {}
    meta = _listing_meta(listing)
    area_info = props.get("resultsAreaInfo") if isinstance(props.get("resultsAreaInfo"), dict) else {}
    city = _clean(props.get("ct")) or _clean(listing.get("pcity"))
    results = [parse_listing_row(r) for r in _rows(listing.get("results"))]
    return {
        "query": meta["keyword"] or _clean(props.get("search")),
        "city": city,
        "area": _clean(area_info.get("area")),
        "title": meta["title"],
        "is_free_text": meta["is_free_text"],
        "category": meta["matched_category"],
        "sort_options": meta["sort_options"],
        "total_count": meta["total"] if meta["total"] is not None else len(meta["docids"]),
        "docids": meta["docids"],
        "search_id": meta["search_id"] or _clean(props.get("search_id")),
        "median_latitude": meta["median_latitude"],
        "median_longitude": meta["median_longitude"],
        "results": results,
    }


def parse_listing_api(payload, city=None):
    """resultsPageListing JSON -> the same shape as parse_search."""
    data = payload or {}
    meta = _listing_meta(data)
    results = [parse_listing_row(r) for r in _rows(data.get("results"))]
    return {
        "query": meta["keyword"],
        "city": city or _clean(data.get("pcity")),
        "area": None,
        "title": meta["title"],
        "is_free_text": meta["is_free_text"],
        "category": meta["matched_category"],
        "sort_options": meta["sort_options"],
        "total_count": meta["total"] if meta["total"] is not None else len(meta["docids"]),
        "docids": meta["docids"],
        "search_id": meta["search_id"],
        "median_latitude": meta["median_latitude"],
        "median_longitude": meta["median_longitude"],
        "results": results,
    }


# ---- the business page -------------------------------------------------------------

def _contact_persons(value):
    out = []
    for item in _split(value):
        match = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", item)
        if match:
            out.append({"name": _clean(match.group(1)), "role": _clean(match.group(2))})
        else:
            out.append({"name": item, "role": None})
    return out


def _social_links(value):
    links = []
    for item in re.split(r"\|~\|", str(value or "")):
        text = _clean(item)
        if not text or "." not in text:
            continue
        link = text if text.startswith(("http://", "https://")) else "https://" + text.lstrip("/")
        if link not in links:
            links.append(link)
    return links


def _websites(value):
    out = []
    for item in _split(value):
        site = item if item.startswith(("http://", "https://")) else "https://" + item
        if site not in out:
            out.append(site)
    return out


def _hours(record):
    hop = record.get("hop") if isinstance(record.get("hop"), dict) else {}
    opstring = record.get("opstring") if isinstance(record.get("opstring"), dict) else {}
    days = []
    for day in hop.get("hop") or []:
        if not isinstance(day, dict):
            continue
        label = _clean(day.get("day")) or ""
        hours = [h for h in _split(day.get("hours_array")) if h] or _split(day.get("hours"))
        days.append({
            "day": re.sub(r"\s*\(today\)\s*", "", label, flags=re.I).strip() or None,
            "is_today": _flag(day.get("today")),
            "hours": hours,
            "is_holiday": _flag(day.get("holiday")),
        })
    return {
        "text": _clean(record.get("HoursOfOperation")),
        "status": _status(hop.get("status")) or _status(opstring.get("status")),
        "today": _clean(opstring.get("timing")),
        "is_open_now": _flag(record.get("opennow")),
        "days": days,
    }


def _attributes(record):
    attrstr = record.get("attrstr") if isinstance(record.get("attrstr"), dict) else {}
    keys = attrstr.get("key") or []
    out = []
    for row in attrstr.get("val") or []:
        if not isinstance(row, list):
            continue
        item = dict(zip(keys, row))
        name, value = _clean(item.get("name")), _clean(item.get("val"))
        if name and value:
            out.append({"name": name, "value": ", ".join(p.strip() for p in value.split("$#$") if p.strip())})
    return out


def _attribute(attributes, name):
    return next((a["value"] for a in attributes if a["name"].lower() == name.lower()), None)


def _services(record):
    services = record.get("services")
    if not isinstance(services, dict):
        return []
    out = []
    for group, items in services.items():
        names = []
        for item in items or []:
            if isinstance(item, dict):
                name = _clean(item.get("att"))
                if name and (item.get("avl") in (None, "", "1", 1, True)):
                    value = _clean(item.get("val"))
                    names.append(f"{name}: {value}" if value else name)
            elif isinstance(item, str) and _clean(item):
                names.append(_clean(item))
        if names:
            out.append({"group": _clean(group), "items": names})
    return out


def _quick_info(record):
    out, summary = [], None
    for item in record.get("quickinfo") or []:
        if not isinstance(item, dict):
            continue
        name, value = _clean(item.get("name")), _clean(item.get("value"))
        if not (name and value):
            continue
        if name.lower() == "business summary":
            summary = summary or value
            continue
        out.append({"name": name, "value": value})
    return summary, out


def _highlights(record):
    data = record.get("pros_cons_data") if isinstance(record.get("pros_cons_data"), dict) else {}
    pros = data.get("pros") if isinstance(data.get("pros"), dict) else {}
    cons = data.get("cons") if isinstance(data.get("cons"), dict) else {}
    liked = [_clean(v) for v in (pros.get("values") or []) if _clean(v)]
    disliked = [_clean(v) for v in (cons.get("values") or []) if _clean(v)]
    if not (liked or disliked):
        return None
    return {"liked": liked, "disliked": disliked}


def _media(record):
    catalogue = record.get("Catalogue") if isinstance(record.get("Catalogue"), dict) else {}
    logo = _clean((record.get("d_logo") or "").split("|$%$|")[0]) if isinstance(record.get("d_logo"), str) else None
    menu_images = []
    for item in record.get("menu_d") or []:
        if not isinstance(item, str):
            continue
        parts = item.split("|-|")
        link = _abs(parts[0])
        if link:
            menu_images.append({"link": link, "tab": _clean(parts[2]) if len(parts) > 2 else None})
    videos = []
    for video in _json(record.get("vdo"), []) or []:
        if isinstance(video, dict) and _clean(video.get("url")):
            videos.append({"link": _abs(video.get("url")), "thumbnail": _abs(video.get("thumb")),
                           "type": _clean(video.get("type"))})
    tabs = []
    for tab in record.get("catalog_slider") or []:
        if isinstance(tab, dict) and _clean(tab.get("label")):
            tabs.append({"name": _clean(tab.get("label")), "count": _int(tab.get("img_cnt"))})
    return {
        "logo": _abs(logo) if logo else None,
        "cover_image": _abs(record.get("disp_pic")),
        "checkin_image": _abs(record.get("jadoopic")),
        "photos_count": _int(catalogue.get("tot")),
        "photo_tabs": tabs,
        "menu_images": menu_images,
        "videos": videos,
    }


def _offers(record):
    out = []
    for offer in record.get("vendor_offers") or []:
        if not isinstance(offer, dict):
            continue
        out.append({
            "id": _clean(offer.get("offerid")),
            "title": _clean(offer.get("heading")),
            "description": _clean(offer.get("desc")),
            "image": _abs(offer.get("img")),
            "valid_until": parse_long_date(offer.get("date")),
            "status": _clean(offer.get("status")),
        })
    return out


def _ordering_links(record):
    data = _json(record.get("tp_rd_url"), {}) or {}
    keys = data.get("key") or []
    out = []
    for row in data.get("val") or []:
        if not isinstance(row, list):
            continue
        item = dict(zip(keys, row))
        link = _abs(item.get("url"))
        if link:
            out.append({"name": _clean(item.get("tag")), "link": link, "offer": _clean(item.get("dis_d"))})
    return out


def _page_reviews(record):
    out = []
    for review in record.get("Reviews") or []:
        if not isinstance(review, dict):
            continue
        out.append({
            "author": _clean(review.get("reviewer_name")),
            "avatar": _abs(review.get("reviewer_photo")),
            "rating": _rating(review.get("review_rate")),
            "date": parse_long_date(review.get("time")),
            "text": _clean(review.get("review_text")),
        })
    return out


def _response_rate(record):
    data = record.get("resp_rate") if isinstance(record.get("resp_rate"), dict) else {}
    enquiries, responded = _int(data.get("lead")), _int(data.get("lead_resp"))
    if enquiries is None and responded is None:
        return None
    return {"enquiries": enquiries, "responded": responded}


def parse_detail(page_props):
    """detailspage.json pageProps -> the full business record."""
    props = page_props or {}
    results = props.get("results") if isinstance(props.get("results"), dict) else {}
    record = results.get("results") if isinstance(results.get("results"), dict) else {}
    event = record.get("event_data") if isinstance(record.get("event_data"), dict) else {}
    og = record.get("og_tags") if isinstance(record.get("og_tags"), dict) else {}
    attr = record.get("attr_data") if isinstance(record.get("attr_data"), dict) else {}
    docid = normalize_docid(_clean(record.get("docid") or event.get("docid") or props.get("docid") or "")) or None
    city = _clean(record.get("city")) or _clean(record.get("data_city"))
    name = _clean(record.get("name"))
    hot = [_clean(p) for p in str(record.get("hot_cat_info") or "").split("|")]
    hcat = record.get("hcatarr") if isinstance(record.get("hcatarr"), dict) else {}
    category = (_category_from(hot[2], hot[1], city, _clean(record.get("area"))) if len(hot) > 2 and hot[2]
                else _category_from(hcat.get("name"), hcat.get("nid") or hcat.get("id"), city, _clean(record.get("area"))))
    categories = []
    for item in record.get("AlsoListedIn") or []:
        if isinstance(item, dict):
            cat = _category_from(item.get("category"), item.get("nid") or item.get("catid"), link=item.get("url"))
            if cat:
                categories.append(cat)
    msg_num = _json(record.get("msg_num"), {}) or {}
    whatsapp = next((w for w in _split(msg_num.get("wup")) if w), None) \
        or next((w for w in _split(record.get("wpnumber")) if w), None)
    summary, quick_info = _quick_info(record)
    jdtrust = record.get("jdtrust") if isinstance(record.get("jdtrust"), list) else []
    paid = _clean(record.get("paidstatus"))
    attributes = _attributes(record)
    node2 = _clean(attr.get("node2")) or ""
    price_for_two = (_inr(node2) if "for two" in node2.lower() else None) or _inr(_attribute(attributes, "Average Cost"))
    link = (_abs(og.get("url") or og.get("link")) or company_link(docid, city, name)) if docid else None
    code = _clean(record.get("sot_code")) or _clean(record.get("shorturl"))
    return {
        "id": docid,
        "name": name,
        "legal_name": _clean(record.get("legal_companyname")),
        "link": link,
        "short_link": short_link(code),
        "tagline": _clean(record.get("tagline")),
        "summary": summary,
        "category": category,
        "categories": categories,
        "rating": _rating(record.get("rating") or record.get("comprating")),
        "ratings_count": _int(record.get("totJdReviews")) or _int(record.get("totalReviews")),
        "is_verified": _flag(record.get("verified")),
        "is_jd_verified": _flag(event.get("jd_verified")) or bool(jdtrust and _flag(jdtrust[0])),
        "is_jd_trusted": _flag(event.get("trust")) or bool(len(jdtrust) > 1 and _flag(jdtrust[1])),
        "is_claimed": _flag(record.get("isClaim")),
        "is_paid_listing": bool(paid) and paid != "0",
        "is_closed_down": _flag(record.get("closeddown_flag")),
        "contact": {
            "phone": _clean(record.get("VNumber")),
            "whatsapp": whatsapp,
            "landline": _clean(record.get("contact")),
            "mobile": _clean(record.get("mobile")),
            "toll_free": _clean(record.get("tollfree")),
            "fax": _clean(record.get("fax")),
            "email": next(iter(_split(record.get("email"))), None),
            "emails": _split(record.get("email")),
            "website": next(iter(_websites(record.get("website"))), None),
            "websites": _websites(record.get("website")),
            "contact_persons": _contact_persons(record.get("contactperson")),
            "social_links": _social_links(record.get("social_medial_url")),
            "response_rate": _response_rate(record),
        },
        "address": {
            "text": _clean(record.get("address")),
            "building": _clean(record.get("building")),
            "street": _clean(record.get("street")),
            "landmark": _clean(record.get("landmark")),
            "area": _clean(record.get("area")),
            "city": city,
            "state": _clean(record.get("state")),
            "pincode": _clean(record.get("pincode")),
            "latitude": _float(record.get("complat")),
            "longitude": _float(record.get("complong")),
            "is_hidden": _flag(record.get("hide_address")),
        },
        "hours": _hours(record),
        "business": {
            "year_established": _int(record.get("YOE")),
            "years_in_business": _years_in_business(attr.get("node3")),
            "employees": _clean(record.get("no_employee")),
            "turnover": _clean(record.get("turnover")),
            "gst_number": _clean(record.get("gst_d")),
            "qualification": _clean(record.get("qualification")) or _attribute(attributes, "Qualification"),
            "specialization": _attribute(attributes, "Specialization"),
            "consultation_fee": _inr(_attribute(attributes, "Consultation Fee")),
            "payment_modes": _split(record.get("paymentModes")),
            "cuisines": _split(record.get("cuisine")),
            "price_for_two": price_for_two,
            "price_range": _price_band(record.get("price_tag_d")),
            "amenities": _split(record.get("main_amenities")),
            "accreditations": _clean(record.get("accreditations")),
            "certificates": _clean(record.get("certificates")),
        },
        "attributes": attributes,
        "services": _services(record),
        "quick_info": quick_info,
        "highlights": _highlights(record),
        "media": _media(record),
        "offers": _offers(record),
        "ordering_links": _ordering_links(record),
        "latest_reviews": _page_reviews(record),
        "phone_allocation_id": _clean(record.get("alocatedicid")) or _clean(record.get("callalocate")),
    }


# ---- reviews -----------------------------------------------------------------------

def parse_review(review):
    if not isinstance(review, dict):
        return None
    owner = _clean(review.get("ownerComment"))
    tags = [_clean(t.get("name")) for t in review.get("pTags") or [] if isinstance(t, dict) and _clean(t.get("name"))]
    negative = [_clean(t.get("name")) for t in review.get("nTags") or [] if isinstance(t, dict) and _clean(t.get("name"))]
    photos = []
    for photo in review.get("photos") or []:
        link = _abs(photo if isinstance(photo, str) else (photo.get("img_url") or photo.get("url") or photo.get("io") or photo.get("img")) if isinstance(photo, dict) else None)
        if link:
            photos.append(link)
    sub_ratings = []
    for item in review.get("subRating") or []:
        if isinstance(item, dict):
            label = _clean(item.get("name") or item.get("label") or item.get("key"))
            value = _rating(item.get("rating") or item.get("value"))
            if label:
                sub_ratings.append({"name": label, "rating": value})
    return {
        "id": _clean(review.get("revid")),
        "rating": _rating(review.get("rating")),
        "date": iso_from_ms(review.get("age")),
        "text": _clean(review.get("rev")),
        "author": {
            "name": _clean(review.get("name")),
            "avatar": _abs(review.get("dp")),
            "reviews_count": _int(review.get("revCount")),
            "ratings_count": _int(review.get("ratings_count")),
        },
        "tags": tags,
        "negative_tags": negative,
        "sub_ratings": sub_ratings,
        "photos": photos,
        "likes": _int(review.get("like_count")) or 0,
        "comments_count": _int(review.get("comment_count")) or 0,
        "shares_count": _int(review.get("share_count")) or 0,
        "owner_reply": {"text": owner, "date": iso_from_ms(review.get("ownerCreatedAt"))} if owner else None,
        "link": _abs(review.get("share_url")),
    }


def _reviews_block(block):
    """The `reviews` dict of the SSR page or the getratings JSON."""
    data = block.get("data") if isinstance(block.get("data"), dict) else {}
    rows = data.get("rating") if isinstance(data.get("rating"), list) else []
    reviews = [r for r in (parse_review(x) for x in rows) if r]
    sort_options = [_clean(f.get("dp")) for f in block.get("filter") or [] if isinstance(f, dict) and _clean(f.get("dp"))]
    return {
        "total_count": _int(data.get("count")),
        "recent_ratings": [r for r in (_rating(v) for v in data.get("recent_ratings") or []) if r is not None],
        "google_ratings_count": _int(data.get("googleCount")),
        "summary": _clean(block.get("reviewSummary")),
        "sort_options": sort_options,
        "reviews": reviews,
    }


def parse_reviews_page(page_props):
    """detail-reviews.json pageProps -> {company, total_count, reviews, …}."""
    props = page_props or {}
    cmp = props.get("cmpInfo") if isinstance(props.get("cmpInfo"), dict) else {}
    block = props.get("reviews") if isinstance(props.get("reviews"), dict) else {}
    out = _reviews_block(block)
    out["company"] = {
        "id": normalize_docid(_clean(props.get("docid") or "")) or None,
        "name": _clean(cmp.get("name")),
        "rating": _rating(cmp.get("rating")),
        "ratings_count": _int(cmp.get("totJdReviews")) or _int(cmp.get("totalReviews")),
    }
    return out


def parse_reviews_api(payload, docid=None):
    """getratings JSON -> the same shape as parse_reviews_page."""
    out = _reviews_block(payload or {})
    out["company"] = {"id": normalize_docid(docid) if docid else None, "name": None,
                      "rating": None, "ratings_count": None}
    return out


# ---- photos ------------------------------------------------------------------------

def parse_photos(page_props):
    """detail-photos.json pageProps -> {company, total_count, tabs, photos, videos}."""
    props = page_props or {}
    gallery = props.get("gallery") if isinstance(props.get("gallery"), dict) else {}
    tabs = []
    for tab in (gallery.get("tabs") or {}).values() if isinstance(gallery.get("tabs"), dict) else []:
        if isinstance(tab, dict) and _clean(tab.get("name")):
            tabs.append({"name": _clean(tab.get("name")), "count": _int(tab.get("total")),
                         "thumbnail": _abs(tab.get("thumb"))})
    images = gallery.get("images") if isinstance(gallery.get("images"), dict) else {}
    bucket = images.get("All") or (next(iter(images.values()), {}) if images else {})
    host = (bucket.get("dn") or ["https://content.jdmagicbox.com"])[0] if isinstance(bucket, dict) else "https://content.jdmagicbox.com"
    photos, seen = [], set()
    for photo in (bucket.get("res") or []) if isinstance(bucket, dict) else []:
        if not isinstance(photo, dict):
            continue
        path = _clean(photo.get("io"))
        link = _abs(path) if path and path.startswith("http") else (host.rstrip("/") + path if path else None)
        thumb_path = _clean(photo.get("it"))
        thumb = _abs(thumb_path) if thumb_path and thumb_path.startswith("http") else (host.rstrip("/") + thumb_path if thumb_path else None)
        pid = _clean(photo.get("pid"))
        key = pid or link
        if not link or key in seen:
            continue
        seen.add(key)
        photos.append({
            "id": pid,
            "link": link,
            "thumbnail": thumb,
            "tags": [t for t in _split(photo.get("tag_arr")) if t] or _split(photo.get("tag")),
            "source": _clean(photo.get("type")),
            "uploaded_by": _clean(photo.get("upload_by")),
            "date": parse_datetime(photo.get("f_date")),
            "width": _int(photo.get("width")),
            "height": _int(photo.get("height")),
            "caption": _clean(photo.get("altTxt")),
        })
    videos = []
    for video in gallery.get("videos") or []:
        if isinstance(video, dict) and _clean(video.get("url") or video.get("io")):
            videos.append({"link": _abs(video.get("url") or video.get("io")), "thumbnail": _abs(video.get("thumb") or video.get("it")),
                           "type": _clean(video.get("type"))})
    return {
        "company": {
            "id": normalize_docid(_clean(props.get("docid") or "")) or None,
            "name": _clean(gallery.get("name")),
        },
        "total_count": _int(gallery.get("tot")) if _int(gallery.get("tot")) is not None else len(photos),
        "tabs": tabs,
        "photos": photos,
        "videos": videos,
    }


# ---- autosuggest --------------------------------------------------------------------

def parse_suggest(rows, city=None):
    """auto.php results -> categories and businesses matching the term."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        kind = row.get("type")
        name = _clean(row.get("oval")) or _clean(row.get("value"))
        row_city = _clean(row.get("dcity") or row.get("city")) or city
        if kind == 1 or (_clean(row.get("ncatid")) and not str(row.get("id", "")).startswith("0")):
            ncatid = _clean(row.get("ncatid") or row.get("id"))
            out.append({
                "type": "category",
                "name": name,
                "id": ncatid,
                "link": category_link(row_city, name, ncatid),
                "city": row_city,
                "listings_count": (_int(row.get("pc")) or 0) + (_int(row.get("nonpc")) or 0) or None,
                "popularity": _float(row.get("pop")),
            })
        else:
            docid = normalize_docid(_clean(row.get("id")) or "") or None
            if not docid:
                continue
            out.append({
                "type": "business",
                "name": name,
                "id": docid,
                "link": company_link(docid, row_city, name),
                "city": row_city,
                "area": _clean(row.get("areaname")),
                "landmark": _clean(row.get("lmark")),
                "rating": _rating(row.get("avgrate")),
                "ratings_count": _int(row.get("totrate")),
                "is_paid_listing": _flag(row.get("paidstatus")),
                "popularity": _float(row.get("pop")),
            })
    return out


# ---- phone ---------------------------------------------------------------------------

def parse_call_allocate(result):
    """callallocate result -> {"phone", "alternates"}."""
    data = result or {}
    alternates = data.get("an")
    if isinstance(alternates, dict):
        alternates = [v for v in alternates.values() if isinstance(v, str)]
    elif not isinstance(alternates, list):
        alternates = []
    return {"phone": _clean(data.get("vn")), "alternates": [a for a in (_clean(v) for v in alternates) if a]}
