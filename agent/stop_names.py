# route 263's own long-form stop descriptions (area, street, landmark) -
# straight off Transport for Greater Manchester's live-departures page for
# this exact route, not re-derived or guessed. we already know precisely
# where every scheduled stop on THIS route is, so there's no need to ask a
# general-purpose reverse geocoder (agent/geocode.py) to work it out from
# raw lat/lon when we can just look the stop up directly.
#
# built from the Altrincham -> Piccadilly Gardens direction (that's the
# order TfGM's page lists them in) - the return leg mostly reuses the same
# physical stops on the same roads, so this covers both directions. a
# handful of stops unique to the return leg (Tesco, Cavendish Road,
# Sinderland Road, etc - this route doesn't retrace its own steps
# everywhere) aren't in here and just fall back to the plain schedule name.

DESCRIPTIONS = {
    "Altrincham Interchange": "Altrincham Interchange",
    "Station House": "Altrincham, Barrington Road, near Station House",
    "Hazel Road": "Altrincham, Barrington Road, opposite Hazel Road",
    "Police Station": "Altrincham, Barrington Road, outside the Police Station",
    "The Navigation": "Broadheath, Manchester Road, opposite The Navigation",
    "George Richards Way": "Broadheath, Manchester Road, near George Richards Way",
    "Railway Bridge": "Broadheath, Manchester Road, at the Railway Bridge",
    "Trafford College": "Broadheath, Manchester Road, outside Trafford College",
    "De Quincey Road": "Sale, Manchester Road, near De Quincey Road",
    "The Drive": "Sale, Washway Road, near The Drive",
    "Woodhouse Lane": "Sale, Washway Road, near Woodhouse Lane",
    "Homelands Road": "Sale, Washway Road, opposite Homelands Road",
    "Raglan Road": "Sale, Washway Road, opposite Raglan Road",
    "Stanley Mount": "Sale, Washway Road, opposite Stanley Mount",
    "Barkers Lane": "Sale, Washway Road, near Barkers Lane",
    "Broadoaks Road": "Sale, Washway Road, opposite Broadoaks Road",
    "Marks and Spencer": "Sale, Washway Road, by Marks and Spencer (Stop B)",
    "Ashfield Road": "Sale, Cross Street, opposite Ashfield Road",
    "Mersey Road": "Sale, Cross Street, near Mersey Road",
    "Dane Road": "Sale, Cross Street, opposite Dane Road",
    "Crossford Bridge": "Sale, Chester Road, next to Crossford Bridge",
    "Poplar Road": "Stretford, Chester Road, near Poplar Road",
    "Stretford Mall": "Stretford, Chester Road, at Stretford Mall (Stop M)",
    "Stretford Public Hall": "Stretford, Chester Road, at Stretford Public Hall (Stop E)",
    "Sydney Street": "Gorse Hill, Chester Road, near Sydney Street",
    "Davyhulme Road East": "Gorse Hill, Chester Road, near Davyhulme Road East",
    "Thomas Street": "Gorse Hill, Chester Road, near Thomas Street",
    "Taylors Road": "Gorse Hill, Chester Road, near Taylors Road",
    "Greatstone Road": "Old Trafford, Chester Road, opposite Greatstone Road",
    "Warwick Road": "Old Trafford, Chester Road, at Warwick Road (Stop E)",
    "Trafford Bar": "Trafford Bar, Chester Road, opposite Trafford Bar",
    "Henry Street": "Old Trafford, Stretford Road, opposite Henry Street",
    "Henrietta Street": "Old Trafford, Stretford Road, opposite Henrietta Street",
    "Cornbrook Street": "St Georges, Stretford Road, opposite Cornbrook Street",
    "Erskine Street": "St Georges, Stretford Road, next to Erskine Street",
    "Mallow Street": "Hulme, Stretford Road, near Mallow Street",
    "Hulme Park": "Hulme, Stretford Road, outside Hulme Park",
    "Royce Road": "Hulme, Stretford Road, next to Royce Road",
    "Epping Street": "Chorlton upon Medlock, Stretford Road, opposite Epping Street",
    "Cambridge Street": "Chorlton upon Medlock, Booth Street West, next to Cambridge Street",
    "Royal Northern College of Music": "Chorlton upon Medlock, Booth Street West, at the Royal Northern College of Music (Stop A)",
    "Manchester Metropolitan University": "Chorlton upon Medlock, Oxford Road, near Manchester Metropolitan University",
    "Chester Street": "Manchester City Centre, Oxford Road, at Chester Street (Stop C)",
    "Dickinson Street": "Manchester City Centre, Portland Street, near Dickinson Street",
    "Charlotte Street": "Manchester City Centre, Portland Street, at Charlotte Street (Stop CK)",
    "Piccadilly Gardens": "Manchester Piccadilly Gardens bus station",
}


def describe(stop_name):
    return DESCRIPTIONS.get(stop_name, stop_name)


def location_description(prev_stop_name, nearest_candidate_name, nearest_candidate_distance, address, lat, lon):
    # picks the best available way to say "here's where this bus is",
    # cheapest/most-certain first: a stop we actually confirmed this
    # cycle beats a nearby candidate, which beats a live reverse-geocode,
    # which beats bare coordinates. shared by the terminal and pdf reports
    # so they can never describe the same bus differently.
    if prev_stop_name:
        return f"at {describe(prev_stop_name)}"
    if nearest_candidate_name:
        # no leading "near" here - most descriptions already open with
        # their own preposition ("near Sydney Street", "opposite Raglan
        # Road"), so adding one doubles up ("near near Sydney Street")
        return f"{describe(nearest_candidate_name)} ({nearest_candidate_distance:.0f}m away)"
    if address:
        return address
    return f"{lat:.4f}, {lon:.4f}"
