"""Geocoding and POI lookup against the deck's own copies of the planet.

Two services, both running on the machine itself with no network beyond it:

  Nominatim  place names -> coordinates. Used when a name is not in the small
             hand-written table the recogniser matches against, which is most
             names: the table has nine cities in it and the planet has millions.

  Overpass   "what is around here" -> markers. This is the thing behind
             "show cafes on map".

Everything in this module is pure: it builds query strings and reads answers.
The callers (pi-geocode, pi-poi) do the talking. That split is what makes it
testable without either service running, and both services are large enough
that having the logic depend on them would mean never testing it.
"""
import json
import math
import re
import unicodedata
from dataclasses import dataclass

# The offline basemap is planet.pmtiles, which stops at z14. Asking for more
# shows the same tiles stretched, which reads as a bug rather than as detail.
MAX_ZOOM = 14
MIN_ZOOM = 1


def zoom_for_bbox(south, north, west, east):
    """Pick a zoom that fits the box on a roughly 720x480 screen.

    Web-mercator zoom z shows 360/2^z degrees of longitude across the world's
    width, so the fit is a log. Latitude is compared against a smaller span
    because the deck's screen is wider than it is tall.
    """
    dlat = abs(north - south)
    dlon = abs(east - west)
    if dlat <= 0 and dlon <= 0:
        return 12          # a point: a neighbourhood view is the useful default
    # Guard the log against zero on one axis (a bbox can be flat in one).
    dlat = max(dlat, 1e-6)
    dlon = max(dlon, 1e-6)
    z_lon = math.log2(360.0 / dlon)
    z_lat = math.log2(170.0 / dlat)
    z = int(min(z_lon, z_lat))
    return max(MIN_ZOOM, min(MAX_ZOOM, z))


# Types that name somewhere people live, as opposed to a unit of administration
# drawn around them. A bare place name means the settlement: someone saying
# "kyoto" means 京都市, not 京都府, even though the prefecture scores higher.
# How far below the best result a settlement may be and still be preferred.
# The importance values on this import are derived from place_rank alone and
# come in steps of about 0.08, so this is "one step". 京都市 sits one step under
# 京都府 and is the intended answer; the hamlets called Tokyo in Benin and Papua
# New Guinea sit two steps under 東京都 and are not.
SETTLEMENT_SLACK = 0.1

SETTLEMENT_TYPES = {
    "city", "town", "village", "hamlet", "municipality", "borough",
    "suburb", "quarter", "neighbourhood", "city_district", "locality",
}

# How far out a place of a given size may arrive. 東京都 stretches a thousand
# kilometres south to Ogasawara, so its bounding box fits at zoom 3 -- which
# shows the whole of Japan and answers a different question than the one asked.
# Keyed by the largest place_rank the floor applies to.
ZOOM_FLOORS = ((4, 3), (8, 7), (12, 8), (16, 9), (30, 10))


def _zoom_floor(place_rank):
    for rank, floor in ZOOM_FLOORS:
        if place_rank <= rank:
            return floor
    return MIN_ZOOM


def _best(results):
    """The result a person meant, which is not the one Nominatim lists first.

    Measured on the deck: "kyoto" comes back with a neighbourhood in Indonesia
    ahead of 京都府, and "tokyo" with a hamlet in Benin ahead of 東京都. The
    ordering is by how well the text matched, which for a bare place name says
    almost nothing. The importance field, meanwhile, is right: 0.2933 for the
    prefecture against 0.1333 for the neighbourhood.

    Ties go to the lower place_rank -- a bare name usually means the bigger
    place. Responses with no importance at all keep the order they came in.
    """
    if not any("importance" in r for r in results if isinstance(r, dict)):
        return results[0]

    def key(r):
        if not isinstance(r, dict):
            return (0, -1.0, 0)
        try:
            importance = float(r.get("importance", 0.0))
        except (TypeError, ValueError):
            importance = 0.0
        try:
            rank = int(r.get("place_rank", 30))
        except (TypeError, ValueError):
            rank = 30
        return (importance, -rank)

    best = max(results, key=key)
    # Among places of comparable standing, prefer the one people live in over
    # the administrative unit drawn around it.
    cutoff = key(best)[0] - SETTLEMENT_SLACK
    nearby = [r for r in results
              if isinstance(r, dict)
              and r.get("addresstype") in SETTLEMENT_TYPES
              and key(r)[0] >= cutoff]
    return max(nearby, key=key) if nearby else best


@dataclass
class Place:
    lat: float
    lon: float
    zoom: int
    name: str

    def flyto_line(self):
        """The line /dev/shm/pi-map-flyto wants: 'lat lon zoom'."""
        return "%.6f %.6f %d" % (self.lat, self.lon, self.zoom)


def parse_nominatim(body):
    """First result of a Nominatim /search response, or None.

    Returns None rather than raising for anything unexpected: this is fed
    whatever the recogniser heard, and a bad answer must not stop the voice
    loop. "Show me the map of Xyzzy" should be a shrug, not a crash.
    """
    try:
        results = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(results, list) or not results:
        return None
    r = _best(results)
    try:
        lat = float(r["lat"])
        lon = float(r["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    try:
        rank = int(r.get("place_rank", 16))
    except (TypeError, ValueError):
        rank = 16
    bbox = r.get("boundingbox")
    if bbox and len(bbox) == 4:
        try:
            s, n, w, e = (float(x) for x in bbox)
            zoom = max(zoom_for_bbox(s, n, w, e), _zoom_floor(rank))
        except (TypeError, ValueError):
            zoom = 12
    else:
        zoom = max(12, _zoom_floor(rank))
    zoom = min(zoom, MAX_ZOOM)
    return Place(lat, lon, zoom, r.get("display_name", ""))


# What a person is likely to say -> the OSM tag that means it. Kept small and
# explicit: an unknown word is refused rather than guessed at, because a guessed
# tag returns an empty map and no explanation of why.
CATEGORIES = {
    "cafe": "amenity=cafe",
    "coffee": "amenity=cafe",
    "カフェ": "amenity=cafe",
    "喫茶店": "amenity=cafe",
    "restaurant": "amenity=restaurant",
    "レストラン": "amenity=restaurant",
    "食堂": "amenity=restaurant",
    "bar": "amenity=bar",
    "pub": "amenity=pub",
    "fastfood": "amenity=fast_food",
    "toilet": "amenity=toilets",
    "トイレ": "amenity=toilets",
    "hospital": "amenity=hospital",
    "病院": "amenity=hospital",
    "pharmacy": "amenity=pharmacy",
    "薬局": "amenity=pharmacy",
    "school": "amenity=school",
    "学校": "amenity=school",
    "bank": "amenity=bank",
    "銀行": "amenity=bank",
    "atm": "amenity=atm",
    "fuel": "amenity=fuel",
    "parking": "amenity=parking",
    "駐車場": "amenity=parking",
    "convenience": "shop=convenience",
    "コンビニ": "shop=convenience",
    "supermarket": "shop=supermarket",
    "スーパー": "shop=supermarket",
    "hotel": "tourism=hotel",
    "ホテル": "tourism=hotel",
    "museum": "tourism=museum",
    "博物館": "tourism=museum",
    "美術館": "tourism=museum",
    "station": "railway=station",
    "駅": "railway=station",
    "park": "leisure=park",
    "公園": "leisure=park",
}


def normalise_category(word):
    """Fold what was said down to a dictionary key.

    Speech gives back "Cafes", "CAFÉ", "cafe." and カフェ for the same thing, so
    strip accents, case, punctuation and a trailing plural before looking up.
    """
    # Strip Latin accents (café -> cafe) but NOT Japanese voiced marks. NFKD
    # decomposes ビ into ヒ + U+3099, and dropping every combining character
    # then turns コンビニ into コンヒニ, which matches nothing. The voiced marks
    # live at U+3099/U+309A, so keep anything at or above U+3000 and recompose.
    s = unicodedata.normalize("NFKC", str(word)).strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s
                if not (unicodedata.combining(c) and c < "\u3000"))
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"[^0-9a-z぀-ヿ一-鿿]+", "", s)
    if s in CATEGORIES:
        return s
    if s.endswith("s") and s[:-1] in CATEGORIES:
        return s[:-1]
    return None


def read_conf(path):
    """A shell-style KEY=value file, as a dict. Missing file is empty.

    systemd's EnvironmentFile only reaches the unit's own process, and these
    tools are run from the voice loop and from a shell as well. Configuring a
    home position in /etc/default and having it silently ignored is exactly the
    kind of failure that looks like the setting not working.
    """
    out = {}
    try:
        with open(path) as fh:
            lines = fh.readlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _read_position(path):
    """First two numbers in a file, if they are a plausible position.

    A garbled file can parse as two floats and still not be anywhere -- the
    range check is what stops "999 999" from becoming a query.
    """
    try:
        with open(path) as fh:
            parts = fh.read().split()
        lat, lon = float(parts[0]), float(parts[1])
    except (OSError, IndexError, ValueError):
        return None
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return (lat, lon)
    return None


def where_now(flyto="/dev/shm/pi-map-flyto",
              gps="/dev/shm/pi-gps-lastfix",
              home=None):
    """Where "here" is when the command named no place, and how we know.

    Returns (lat, lon, source) or None.

    "show cafes on map" is the common case and it names nowhere. The map does
    not publish its camera, and after a reboot it is showing the whole world
    (the style starts at 0,0 zoom 1) with /dev/shm empty, so there is genuinely
    nothing to centre on. In order of how likely each is to be what the person
    means:

      flyto   where the map was last sent. Almost always right: "show cafes"
              follows "show me <place>".
      gps     where the deck itself is. Right when nobody has moved the map.
      home    a coordinate someone configured, for a deck with no GPS fix.

    Returning None rather than guessing a city is deliberate: being told the
    map has not been anywhere is better than being shown cafes in a place the
    person has never been.
    """
    for path, name in ((flyto, "map"), (gps, "gps")):
        if path:
            pos = _read_position(path)
            if pos:
                return (pos[0], pos[1], name)
    if home:
        import io
        try:
            lat, lon = (float(x) for x in str(home).split()[:2])
        except (IndexError, ValueError):
            return None
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return (lat, lon, "home")
    return None


def canonical_category(word):
    """The English name for whatever was said, or None.

    カフェ and "coffee" and "Cafes" all mean amenity=cafe; downstream should see
    one spelling so that nothing below this point has to know two languages.
    """
    key = normalise_category(word)
    if key is None:
        return None
    tag = CATEGORIES[key]
    for name, other in CATEGORIES.items():
        if other == tag and name.isascii():
            return name
    return key


def overpass_query(category, lat, lon, radius_m, timeout=30):
    """An Overpass QL query for one category within radius_m of a point.

    A bounding box rather than `around:`: the box is what the map is showing,
    and Overpass answers box queries from its spatial index without measuring
    distances. Ways and relations are included with `center`, since a cafe is
    as likely to be a building outline as a node.
    """
    key = normalise_category(category)
    if key is None:
        raise ValueError(
            "unknown category %r -- add it to geo.CATEGORIES rather than "
            "guessing a tag" % (category,))
    tag = CATEGORIES[key]
    dlat = radius_m / 111_320.0
    # Longitude degrees shrink towards the poles; without the cos the box is far
    # too wide in Hokkaido and too narrow near the equator.
    dlon = radius_m / (111_320.0 * max(0.05, math.cos(math.radians(lat))))
    s, n = lat - dlat, lat + dlat
    w, e = lon - dlon, lon + dlon
    box = "(%.6f,%.6f,%.6f,%.6f)" % (s, w, n, e)
    k, v = tag.split("=", 1)
    return ("[out:json][timeout:%d];"
            "(node[%s=%s]%s;way[%s=%s]%s;);"
            "out center;" % (timeout, k, v, box, k, v, box))


def _safe_label(name, fallback):
    """A single whitespace-free token.

    The map parses the marker line with one `>>` per field, so a space in the
    name would truncate the label and leave the rest of the line unparsed.
    """
    if not name:
        return fallback
    token = re.sub(r"\s+", "_", str(name).strip())
    return token or fallback


def marker_lines(body, epoch, limit=200):
    """Turn an Overpass answer into the marker feed the map already reads.

    The format is the Meshtastic node feed's: '<id> <lat> <lon> <epoch> <name>'.
    Reusing it means POIs appear with no change to the map at all -- and on a
    deck with no radio, nothing else is using it. The ids are prefixed so that
    on a deck that does have one, the two feeds cannot collide.

    Silent on malformed input, for the same reason parse_nominatim is.
    """
    try:
        doc = json.loads(body)
        elements = doc["elements"]
    except (ValueError, TypeError, KeyError):
        return ""
    out = []
    for el in elements:
        if len(out) >= limit:
            break
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            centre = el.get("center") or {}
            lat = centre.get("lat")
            lon = centre.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags") or {}
        label = _safe_label(tags.get("name"), tags.get("amenity")
                            or tags.get("shop") or "poi")
        out.append("poi%s %.6f %.6f %d %s"
                   % (el.get("id", len(out)), float(lat), float(lon),
                      int(epoch), label))
    return "\n".join(out)
