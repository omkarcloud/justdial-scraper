"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json.
"""
import json
import os

from justdial.api import companies_search, company_details, company_reviews
from justdial.refs import resolve_query

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # a business ID, any justdial.com business link or a DT short link
    save("company_taj_mahal_palace.json", company_details("022P2000023"))

    # 10 businesses per page, up to 100 per search
    save("search_software_companies_bengaluru.json",
         companies_search(query=resolve_query("Software Companies"), city="Bengaluru"))

    # 10 reviews per page, newest first
    save("reviews_taj_mahal_palace.json", company_reviews("022P2000023", sort="re"))
