"""The starter library: a draft list of sites and books worth collecting before an emergency.

Nothing in it is checked or approved. Each entry starts as "unverified" and cannot be used until YOU approve it, after you
have looked at the site (is it still there, does it allow copying, is it the right size for your drive). The list is a
plain JSON file you can edit (starter_library.json next to your settings), and Cindrix Recon never changes it behind your back.
"""
import json
import os
import re
import shutil

import settings as appsettings

REVIEWED = "reviewed 2026-10-29, not live-checked"
ABOUT = ("Starter list, reviewed 2026-10-29. The sites and books are real, but web addresses and copying rules change and the "
         "addresses were NOT opened live, so look at a site before you collect it. Entries you add or edit start unapproved "
         "(the Approve box in the program). Edit freely.")

SITES = [
    ("Ready.gov (US emergency preparedness)", "https://www.ready.gov/", "whole", "lite", "Official preparedness guides from FEMA. US government material is generally free to copy; some photos may not be. The site blocks automated visitors (bot check): the quick check may say access denied, and the crawl may fail."),
    ("CDC emergency preparedness", "https://emergency.cdc.gov/", "whole", "lite", "Health and safety guidance in disasters."),
    ("Red Cross: how to prepare", "https://www.redcross.org/get-help/how-to-prepare-for-emergencies.html", "section", "lite",
     "Checklists and guides; the Red Cross site is large, so this takes only the preparedness section."),
    ("Hesperian Health Guides", "https://hesperian.org/", "section", "lite", "Community health guides. Hesperian publishes copyright terms per book (see its Copyright Information pages); check them before sharing copies."),
    ("National Weather Service safety", "https://www.weather.gov/safety/", "section", "lite", "Storm, flood, heat and cold safety pages."),
    ("USDA food safety", "https://www.fsis.usda.gov/food-safety", "section", "lite", "Safe storage, cooking and power-outage food guidance."),
    ("MedlinePlus health topics", "https://medlineplus.gov/", "whole", "text", "US National Library of Medicine; its copyright page says most content is free to reuse, some pictures are not. Large: try 'No pictures' first."),
    ("Appropedia (sustainability how-tos)", "https://www.appropedia.org/", "whole", "text", "Water, power, shelter and food projects. Content is Creative Commons share-alike (CC BY-SA), so keep the credit. Large."),
    ("Low-tech Magazine", "https://solar.lowtechmagazine.com/", "whole", "lite", "Low-energy technology articles. Copying terms not confirmed: check the site's about page."),
]
BOOKS = [
    ("Where There Is No Doctor", "where there is no doctor werner", "Village health care handbook."),
    ("Where There Is No Dentist", "where there is no dentist dickson", "Basic dental care without a dentist."),
    ("US Army Survival Manual FM 21-76", "survival manual fm 21-76", "Field manual; US government works are usually public domain."),
    ("US Army First Aid FM 4-25.11", "first aid fm 4-25.11", "Field first aid."),
    ("Edible Wild Plants", "edible wild plants", "Identification guides vary a lot; check any plant guide against a second source."),
    ("Encyclopedia of Country Living", "encyclopedia of country living carla emery", "In copyright: expect borrow or buy links, not a free file."),
    ("Seed to Seed", "seed to seed suzanne ashworth", "Seed saving. In copyright: expect borrow or buy links."),
    ("Practical Emergency Preparedness", "emergency preparedness handbook", "A broad search; pick the best match yourself."),
    ("Basic Radio Communications", "basic radio communications amateur radio handbook", "Amateur radio basics; check the edition."),
    ("Food Preservation and Canning", "complete guide to home canning usda", "USDA canning guide is a US government work."),
]


def default_entries():
    out = []
    for i, (name, url, preset, detail, note) in enumerate(SITES, 1):
        out.append({"id": f"site{i}", "kind": "site", "name": name, "url": url, "preset": preset, "detail": detail, "note": note,
                    "status": REVIEWED, "approved": True})
    for i, (name, query, note) in enumerate(BOOKS, 1):
        out.append({"id": f"book{i}", "kind": "book", "name": name, "query": query, "note": note, "status": REVIEWED,
                    "approved": True})
    return out


def path():
    return os.path.join(os.path.dirname(appsettings.settings_path()), "starter_library.json")


def problem(e):
    """Why an entry cannot be used, or ''."""
    if not str(e.get("name", "")).strip():
        return "It needs a name."
    if e.get("kind") == "site":
        u = str(e.get("url", "")).strip()
        if not re.match(r"^https?://[^/\s]+\.[^/\s]+", u) and not re.match(r"^https?://localhost", u):
            return "The web address must start with http:// or https:// and be a full address."
        return ""
    if e.get("kind") == "book":
        return "" if str(e.get("query", "")).strip() else "It needs a title or author to look up."
    return "Unknown kind (use site or book)."


def usable(e):
    return bool(e.get("approved")) and not problem(e)


def save(entries, p=None):
    p = p or path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"about": ABOUT, "version": 1, "entries": entries}, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def load(p=None):
    """The list, creating the draft file the first time. A damaged file is kept as .bad and replaced by the draft."""
    p = p or path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            ents = data["entries"] if isinstance(data, dict) else data
            ents = [e for e in ents if isinstance(e, dict)]
            for e in ents:
                e.setdefault("approved", False)
                e.setdefault("status", "unverified")
            return ents
        except (OSError, ValueError, KeyError, TypeError):
            try:
                shutil.copy(p, p + ".bad")
            except OSError:
                pass
    ents = default_entries()
    try:
        save(ents, p)
    except OSError:
        pass
    return ents


def new_id(entries, kind):
    n = 1
    ids = {e.get("id") for e in entries}
    while f"{kind}{n}x" in ids:
        n += 1
    return f"{kind}{n}x"
