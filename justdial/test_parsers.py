"""Offline tests for the Justdial parsers, reference resolvers and value
helpers.

    python -m pytest justdial/test_parsers.py -q

Fixtures are real www.justdial.com payloads captured 2026-10-26
(justdial/fixtures/, the __NEXT_DATA__ pageProps of the data routes and
the JSON of the signed /api/* routes + autosuggest). No network.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from justdial import parsers as P, refs  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
MAO = "022PXX22.XX22.140804130028.J6U9"
BADEMIYA = "022PXX22.XX22.260604114558.W5C1"


def fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def category_page():
    return P.parse_search(fixture("results_category_mumbai_restaurants.json"))


@pytest.fixture(scope="module")
def paid():
    return P.parse_detail(fixture("detail_paid_restaurant.json"))


@pytest.fixture(scope="module")
def free():
    return P.parse_detail(fixture("detail_free_listing.json"))


@pytest.fixture(scope="module")
def doctor():
    return P.parse_detail(fixture("detail_doctor.json"))


# ---- refs ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (MAO, MAO),
    ("022pxx22.xx22.140804130028.j6u9", MAO),
    ("022PXX22-XX22-140804130028-J6U9", MAO),
    ("022P9830068", "022P9830068"),
    ("https://www.justdial.com/Mumbai/Mao-Family-Restaurant-Kalbadevi/022PXX22-XX22-140804130028-J6U9_BZDET", MAO),
    ("https://www.justdial.com/hi/Mumbai/x/022PXX22-XX22-140804130028-J6U9_BZDET/reviews?x=1", MAO),
    ("https://justdial.com/Mumbai/Shahi-Restaurant/022P9830068_BZDET", "022P9830068"),
    ("DT-43RCW5QC", "DT-43RCW5QC"),
    ("dt-43rcw5qc", "DT-43RCW5QC"),
    ("https://www.justdial.com/DT-43RCW5QC", "DT-43RCW5QC"),
    ("https://jsdl.in/DT-43RCW5QC", "DT-43RCW5QC"),
])
def test_resolve_company(value, expected):
    assert refs.resolve_company(value) == expected


@pytest.mark.parametrize("value", [
    "", "mao family", "12345", "nct-10408936",
    "https://example.com/Mumbai/x/022PXX22-XX22-140804130028-J6U9_BZDET",
    "https://www.justdial.com/Mumbai/Restaurants/nct-10408936",
])
def test_resolve_company_invalid(value):
    with pytest.raises(ValueError):
        refs.resolve_company(value)


def test_is_short_code():
    assert refs.is_short_code("DT-43RCW5QC")
    assert not refs.is_short_code(MAO)


@pytest.mark.parametrize("value,expected", [
    ("Dentists", {"text": "Dentists", "ncatid": None, "city": None, "area": None}),
    ("nct-10156331", {"text": None, "ncatid": "10156331", "city": None, "area": None}),
    ("https://www.justdial.com/Mumbai/Restaurants/nct-10408936",
     {"text": "Restaurants", "ncatid": "10408936", "city": "Mumbai", "area": None}),
    ("https://www.justdial.com/Mumbai/Restaurants-in-Andheri-West/nct-10408936/page-2",
     {"text": "Restaurants", "ncatid": "10408936", "city": "Mumbai", "area": "Andheri West"}),
    ("https://www.justdial.com/hi/New-Delhi/Dentists/nct-10156331?trkid=x",
     {"text": "Dentists", "ncatid": "10156331", "city": "New Delhi", "area": None}),
])
def test_resolve_query(value, expected):
    assert refs.resolve_query(value) == expected


@pytest.mark.parametrize("value", [
    "", "https://example.com/Mumbai/Restaurants/nct-10408936",
    "https://www.justdial.com/Mumbai/x/022PXX22-XX22-140804130028-J6U9_BZDET",
])
def test_resolve_query_invalid(value):
    with pytest.raises(ValueError):
        refs.resolve_query(value)


def test_links():
    assert refs.search_slug("Interior Designers", "Andheri West") == "Interior-Designers-in-Andheri-West"
    assert refs.company_link(MAO, "Mumbai", "Mao Family Restaurant") == \
        "https://www.justdial.com/Mumbai/Mao-Family-Restaurant/022PXX22-XX22-140804130028-J6U9_BZDET"
    assert refs.company_link(MAO, path="Thane/X-Y/022PXX22-XX22-140804130028-J6U9_BZDET").startswith(
        "https://www.justdial.com/Thane/")
    assert refs.short_link("43RCW5QC") == "https://www.justdial.com/DT-43RCW5QC"
    assert refs.short_link("") is None
    assert refs.category_link("Mumbai", "Restaurants", "10408936", "Andheri West") == \
        "https://www.justdial.com/Mumbai/Restaurants-in-Andheri-West/nct-10408936"


# ---- value helpers -----------------------------------------------------------------

def test_value_helpers():
    assert P._int("24,828 Ratings") == 24828
    assert P._float("4.2") == 4.2
    assert P._clean('<span class="x">Open</span> <span> until 1:00 am</span>') == "Open until 1:00 am"
    assert P._clean("xxxxxxx") is None
    assert P._clean("") is None
    assert P.parse_long_date("22nd October, 2026") == "2026-10-22"
    assert P.parse_long_date("Last Date: 30 Apr 2027") == "2027-04-30"
    assert P.parse_long_date("") is None
    assert P.iso_from_ms(1792668995000) == "2026-10-22T11:36:35Z"
    assert P.iso_from_ms(0) is None
    assert P.parse_datetime("2020-07-28 20:09:12") == "2020-07-28T20:09:12"
    assert P._inr("₹ 650 Price for two") == {"amount": 650, "currency": "INR"}
    assert P._price_band("0-500") == {"min": 0, "max": 500, "currency": "INR"}
    assert P._price_band("") is None
    assert P._years_in_business(["12 Years in Business", 1, 1]) == 12
    assert P._contact_persons("Mr Janak Rathod (Director),Ms X") == [
        {"name": "Mr Janak Rathod", "role": "Director"}, {"name": "Ms X", "role": None}]
    assert P._social_links("www.facebook.com/mao|~||~|") == ["https://www.facebook.com/mao"]


# ---- search ------------------------------------------------------------------------

def test_search_category_page(category_page):
    page = category_page
    assert page["city"] == "Mumbai" and page["query"] == "Restaurants"
    assert page["is_free_text"] is False
    assert page["category"] == {"name": "Restaurants", "id": "10408936",
                                "link": "https://www.justdial.com/Mumbai/Restaurants/nct-10408936"}
    assert page["total_count"] == 100 and len(page["docids"]) == 100
    assert len(page["search_id"]) == 64
    assert page["sort_options"] == ["distance", "rating", "popular"]
    assert len(page["results"]) == 10
    row = page["results"][0]
    assert row["id"] == "022PXX22.XX22.140725163254.J1I3" and row["name"] == "Ashapura Snacks"
    assert row["link"].endswith("/022PXX22-XX22-140725163254-J1I3_BZDET")
    assert row["link"].startswith("https://www.justdial.com/Thane/")
    assert row["address"]["city"] == "Thane"            # loccity, not the searched city
    assert row["short_link"] == "https://www.justdial.com/DT-43BZIPZ1"
    assert row["category"]["name"] == "Fast Food" and row["category"]["id"] == "10200860"
    assert row["rating"] == 4.0 and row["ratings_count"] == 1639
    assert row["is_verified"] is True and row["is_paid_listing"] is True and row["is_ad"] is False
    assert row["phone"] == "07405552263" and row["whatsapp"] is None
    assert row["address"]["pincode"] == "400607" and abs(row["address"]["latitude"] - 19.2262) < 0.001
    assert row["hours"] == {"status": "Open until 10:00 pm", "today": "7:00 am - 1:00 pm , 4:30 pm - 10:00 pm",
                            "is_open_now": True}
    assert row["price_for_two"] == {"amount": 200, "currency": "INR"}
    assert row["years_in_business"] == 13
    assert row["thumbnail"].startswith("https://content.jdmagicbox.com/")
    assert row["photos_count"] >= 1 and len(row["images"]) >= 1
    assert row["position"] == 1
    assert all(r["id"] and r["name"] and r["link"] for r in page["results"])
    assert all(r["id"] in page["docids"] for r in page["results"])


def test_search_area_and_free_text():
    area = P.parse_search(fixture("results_area_restaurants_andheri_west.json"))
    assert area["area"] == "Andheri West" and area["category"]["id"] == "10408936"
    assert area["title"].startswith("Best Restaurants in Andheri West")
    free = P.parse_search(fixture("results_freetext_bademiya.json"))
    assert free["is_free_text"] is True and free["category"] is None
    assert free["total_count"] == 50 and len(free["docids"]) == 50
    assert free["results"][0]["name"].startswith("Bademiya")
    assert free["results"][0]["phone"] is None          # a free listing prints no number
    small = P.parse_search(fixture("results_small_city_nashik_dentists.json"))
    assert small["city"] == "Nashik" and small["results"][0]["address"]["city"] == "Nashik"


def test_search_sparse_payload():
    page = P.parse_search({"listData": {}})
    assert page["results"] == [] and page["docids"] == [] and page["total_count"] == 0
    assert P.parse_search(None)["results"] == []
    assert P.parse_listing_row({})["id"] is None


def test_listing_api_pages():
    page1 = P.parse_listing_api(fixture("listing_api_page1_sorted_rating.json"), "Mumbai")
    assert page1["total_count"] == 100 and len(page1["docids"]) == 100 and len(page1["results"]) == 10
    assert page1["category"]["id"] == "10408936" and page1["query"] == "Restaurants"
    assert [r["rating"] for r in page1["results"]] == [5.0] * 10     # sorted by rating
    page2 = P.parse_listing_api(fixture("listing_api_page2.json"), "Mumbai")
    assert len(page2["results"]) == 10 and page2["docids"] == []
    assert page2["results"][0]["address"]["city"]


# ---- detail -------------------------------------------------------------------------

def test_detail_paid_listing(paid):
    assert paid["id"] == MAO and paid["name"] == "Mao Family Restaurant"
    assert paid["legal_name"] == "Mao Family Restaurant"
    assert paid["link"] == ("https://www.justdial.com/Mumbai/Mao-Family-Restaurant-Near-Theatre-Kalbadevi/"
                            "022PXX22-XX22-140804130028-J6U9_BZDET")
    assert paid["short_link"] == "https://www.justdial.com/DT-43RCW5QC"
    assert paid["summary"].startswith("Restaurant offering")
    assert paid["category"] == {"name": "Mughlai Restaurants", "id": "10331943",
                                "link": "https://www.justdial.com/Mumbai/Mughlai-Restaurants-in-Kalbadevi/nct-10331943"}
    assert len(paid["categories"]) == 10 and paid["categories"][0]["name"] == "Restaurants"
    assert paid["rating"] == 4.2 and paid["ratings_count"] == 10055
    assert paid["is_verified"] and paid["is_jd_verified"] and paid["is_jd_trusted"] and paid["is_claimed"]
    assert paid["is_paid_listing"] is True and paid["is_closed_down"] is False
    contact = paid["contact"]
    assert contact["phone"] == "09845262099" and contact["whatsapp"] == "9619629860"
    assert contact["email"] == "vickymilf@yahoo.co.in"
    assert contact["website"] == "https://www.maofamilyrestaurant.com"
    assert contact["contact_persons"][0] == {"name": "Mr Janak Chandulal Rathod", "role": "Director"}
    assert contact["social_links"] == ["https://www.facebook.com/maothefamilyrestaurant"]
    assert contact["response_rate"] == {"enquiries": 3802, "responded": 157}
    address = paid["address"]
    assert address["area"] == "Kalbadevi" and address["pincode"] == "400002" and address["state"] == "Maharashtra"
    assert address["latitude"] == 18.944402 and address["longitude"] == 72.829283
    hours = paid["hours"]
    assert hours["text"] == "Mon - Sun :- 11:00 am - 1:00 am" and hours["is_open_now"] is True
    assert hours["status"] == "Open until 1:00 am"
    assert len(hours["days"]) == 7 and hours["days"][0] == {
        "day": "MON", "is_today": True, "hours": ["11:00 am - 1:00 am"], "is_holiday": False}
    business = paid["business"]
    assert business["year_established"] == 2014 and business["years_in_business"] == 12
    assert business["employees"] == "10-100" and business["gst_number"] == "27AAZFM3961D1ZT"
    assert business["payment_modes"] == ["Cash"] and "Mughlai" in business["cuisines"]
    assert business["price_for_two"] == {"amount": 650, "currency": "INR"}
    assert business["price_range"] == {"min": 0, "max": 500, "currency": "INR"}
    assert business["amenities"] == ["Home Delivery", "Noise Level-Moderate", "Good Place for Kids"]
    assert {"name": "Must Order", "value": "Caramel Custard"} in paid["attributes"]
    assert paid["services"][0]["group"] == "Serves" and "Biryani" in paid["services"][0]["items"]
    assert {"name": "Year of Establishment", "value": "2014"} in paid["quick_info"]
    assert len(paid["highlights"]["liked"]) == 5
    media = paid["media"]
    assert media["logo"].endswith(".jpg") and media["photos_count"] == 110
    assert media["photo_tabs"][1] == {"name": "Food", "count": 54}
    assert len(media["menu_images"]) == 8 and media["menu_images"][0]["tab"] == "All"
    assert media["videos"][0]["type"] == "slideshow" and media["videos"][0]["link"].endswith(".m3u8")
    assert paid["offers"][0]["valid_until"] == "2027-04-30" and paid["offers"][0]["status"] == "Live"
    assert paid["ordering_links"][0]["link"] == "https://maofamilyrestaurant.werafoods.com/"
    assert paid["latest_reviews"][0]["date"] == "2026-10-22" and paid["latest_reviews"][0]["rating"] == 5.0
    assert paid["phone_allocation_id"].startswith("tfJbTArH")


def test_detail_free_listing_is_sparse(free):
    assert free["id"] == BADEMIYA and free["name"] == "Bademiya - Colaba"
    assert free["is_paid_listing"] is False and free["is_claimed"] is False
    assert free["contact"]["phone"] is None and free["contact"]["whatsapp"] is None
    assert free["contact"]["email"] is None and free["contact"]["contact_persons"] == []
    assert free["contact"]["websites"] == ["https://bademiya.org/", "https://www.bademiya.org"]
    assert free["business"]["year_established"] is None and free["business"]["payment_modes"] == []
    assert free["services"] == [] and free["attributes"] == [] and free["offers"] == []
    assert free["media"]["logo"] is None and free["media"]["photos_count"] == 1
    assert free["hours"]["days"] and free["hours"]["status"].startswith("Opens in")
    assert free["phone_allocation_id"].startswith("LGLuodp87s")
    assert free["categories"][0]["name"] == "Kabab Centres"


def test_detail_doctor(doctor):
    assert doctor["name"].startswith("Dr. Madhavi Thoke")
    assert doctor["category"] is not None
    assert doctor["business"]["specialization"] == "General Physician"
    assert doctor["business"]["consultation_fee"] == {"amount": 500, "currency": "INR"}
    assert doctor["business"]["price_for_two"] is None
    assert doctor["business"]["year_established"] == 2003
    assert doctor["services"][0]["group"] == "Treatment"
    assert any(a["name"] == "Registration" and "Maharashtra Medical Council" in a["value"] for a in doctor["attributes"])


def test_detail_sparse_payload():
    record = P.parse_detail({"results": {"results": {"name": "X", "docid": "022P1"}}})
    assert record["id"] == "022P1" and record["name"] == "X"
    assert record["contact"]["phone"] is None and record["hours"]["days"] == []
    assert record["media"]["photos_count"] is None and record["offers"] == []
    assert P.parse_detail({})["id"] is None


# ---- reviews -------------------------------------------------------------------------

def test_reviews_page():
    page = P.parse_reviews_page(fixture("reviews_page1_restaurant.json"))
    assert page["company"] == {"id": MAO, "name": "Mao Family Restaurant", "rating": 4.2, "ratings_count": 10055}
    assert page["total_count"] == 1086 and len(page["reviews"]) == 10
    assert page["recent_ratings"][:3] == [5.0, 5.0, 5.0]
    assert page["sort_options"] == ["Relevant", "Friends Ratings", "Latest", "High to Low"]
    review = page["reviews"][0]
    assert review["id"] == "1792668946408135" and review["rating"] == 5.0
    assert review["date"] == "2026-10-22T11:36:35Z"
    assert review["text"].startswith("I visited Mao Family Restaurant")
    assert review["author"] == {"name": "Mandar Suryavanshi",
                                "avatar": "https://content.jdmagicbox.com/mobile-t/icon/jdsocial/user10.png",
                                "reviews_count": 335, "ratings_count": 23}
    assert review["tags"] == ["Authentic cuisine", "Great service"]
    assert review["owner_reply"] is None and review["likes"] == 0
    assert review["link"] == "https://www.justdial.com/JdSocial/post/1792668946408135"
    page2 = P.parse_reviews_page(fixture("reviews_page2_restaurant.json"))
    assert len(page2["reviews"]) == 10 and page2["reviews"][0]["id"] != review["id"]
    with_photos = [r for r in page2["reviews"] if r["photos"]]
    assert len(with_photos) == 2 and with_photos[0]["photos"][0].startswith("https://images.jdmagicbox.com/")


def test_reviews_empty_and_api():
    empty = P.parse_reviews_page(fixture("reviews_empty_free_listing.json"))
    assert empty["reviews"] == [] and empty["total_count"] is None
    assert empty["company"]["id"] == BADEMIYA and empty["company"]["ratings_count"] == 24828
    api = P.parse_reviews_api(fixture("ratings_api_latest_page1.json"), MAO)
    assert api["total_count"] == 1070 and len(api["reviews"]) == 10
    assert api["company"]["id"] == MAO
    assert P.parse_reviews_page({})["reviews"] == []
    assert P.parse_review(None) is None


# ---- photos ---------------------------------------------------------------------------

def test_photos():
    gallery = P.parse_photos(fixture("photos_restaurant.json"))
    assert gallery["company"] == {"id": MAO, "name": "Mao Family Restaurant"}
    assert gallery["total_count"] == 110 and len(gallery["photos"]) == 110
    assert gallery["tabs"][0]["name"] == "All" and gallery["tabs"][0]["count"] == 110
    photo = gallery["photos"][0]
    assert photo["id"] == "164722078" and photo["link"].endswith("fqj1km1qaf.jpg")
    assert photo["thumbnail"].endswith("-250.jpg") and photo["tags"] == ["food"]
    assert photo["date"] == "2020-07-28T20:09:12" and photo["width"] == 512 and photo["height"] == 367
    assert len(gallery["videos"]) == 1 and gallery["videos"][0]["link"].endswith(".m3u8")
    single = P.parse_photos(fixture("photos_single_free_listing.json"))
    assert single["total_count"] == 1 and len(single["photos"]) == 1 and single["videos"] == []
    assert P.parse_photos({})["photos"] == []


# ---- suggest / phone --------------------------------------------------------------------

def test_suggest():
    rows = P.parse_suggest(fixture("suggest_dent_delhi.json"), "Delhi")
    assert rows[0] == {"type": "category", "name": "Dentists", "id": "10156331",
                       "link": "https://www.justdial.com/Delhi/Dentists/nct-10156331", "city": "Delhi",
                       "listings_count": 33124, "popularity": 24.8976}
    business = [r for r in P.parse_suggest(fixture("suggest_business_mao_mumbai.json"), "Mumbai")
                if r["type"] == "business"]
    assert business[0]["id"] == MAO and business[0]["area"] == "Kalbadevi"
    assert business[0]["rating"] == 4.2 and business[0]["ratings_count"] == 10047
    assert business[0]["link"].endswith("/022PXX22-XX22-140804130028-J6U9_BZDET")
    assert P.parse_suggest(fixture("suggest_nomatch.json"), "Mumbai") == []
    assert P.parse_suggest(None) == []


def test_call_allocate():
    assert P.parse_call_allocate(fixture("callallocate_free_listing.json")) == {
        "phone": "07947135247", "alternates": []}
    assert P.parse_call_allocate({}) == {"phone": None, "alternates": []}
