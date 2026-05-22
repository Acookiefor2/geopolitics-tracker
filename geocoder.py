# geocoder.py
"""
Coordinate resolution with two-layer fallback:
  1. Accept LLM-provided coordinates if they pass a sanity check.
  2. Fall back to Nominatim (OpenStreetMap) geocoding.
  3. Fall back to a hardcoded capital-city lookup table.

This prevents hallucinated coordinates from polluting the map while
keeping the system fully offline-capable via the lookup table.
"""

import logging
import time
from functools import lru_cache
from typing import Any, Optional, Tuple

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

from config import GEOCODE_TIMEOUT, GEOCODE_USER_AGENT

logger = logging.getLogger(__name__)

Coord = Tuple[float, float]   # (latitude, longitude)

# ─── Hardcoded fallback table (capital cities + major geopolitical hubs) ──────
# fmt: off
CAPITAL_COORDS: dict[str, Coord] = {
    "afghanistan": (34.5553, 69.2075),    "albania": (41.3317, 19.8319),
    "algeria": (36.7372, 3.0865),         "angola": (-8.8368, 13.2343),
    "argentina": (-34.6037, -58.3816),    "armenia": (40.1792, 44.4991),
    "australia": (-35.2809, 149.1300),    "austria": (48.2092, 16.3728),
    "azerbaijan": (40.4093, 49.8671),     "bahrain": (26.2154, 50.5832),
    "bangladesh": (23.7104, 90.4074),     "belarus": (53.9045, 27.5615),
    "belgium": (50.8503, 4.3517),         "bolivia": (-16.5000, -68.1500),
    "bosnia": (43.8476, 18.3564),         "brazil": (-15.7797, -47.9297),
    "bulgaria": (42.6977, 23.3219),       "burkina faso": (12.3647, -1.5332),
    "cambodia": (11.5449, 104.9160),      "cameroon": (3.8667, 11.5167),
    "canada": (45.4215, -75.6972),        "central african republic": (4.3612, 18.5550),
    "chad": (12.1048, 15.0445),           "chile": (-33.4569, -70.6483),
    "china": (39.9042, 116.4074),         "colombia": (4.7110, -74.0721),
    "congo": (-4.3217, 15.3222),          "croatia": (45.8150, 15.9819),
    "cuba": (23.1136, -82.3666),          "czech republic": (50.0755, 14.4378),
    "denmark": (55.6761, 12.5683),        "djibouti": (11.5720, 43.1456),
    "dr congo": (-4.3217, 15.3222),       "ecuador": (-0.2299, -78.5249),
    "egypt": (30.0444, 31.2357),          "eritrea": (15.3229, 38.9251),
    "estonia": (59.4370, 24.7536),        "ethiopia": (9.0250, 38.7469),
    "finland": (60.1699, 24.9384),        "france": (48.8566, 2.3522),
    "georgia": (41.6941, 44.8337),        "germany": (52.5200, 13.4050),
    "ghana": (5.5600, -0.2057),           "greece": (37.9838, 23.7275),
    "guatemala": (14.6349, -90.5069),     "guinea": (9.6370, -13.5317),
    "haiti": (18.5944, -72.3074),         "honduras": (14.0818, -87.2068),
    "hungary": (47.4979, 19.0402),        "india": (28.6139, 77.2090),
    "indonesia": (-6.2088, 106.8456),     "iran": (35.6892, 51.3890),
    "iraq": (33.3152, 44.3661),           "ireland": (53.3498, -6.2603),
    "israel": (31.7683, 35.2137),         "italy": (41.9028, 12.4964),
    "japan": (35.6762, 139.6503),         "jordan": (31.9522, 35.9330),
    "kazakhstan": (51.1801, 71.4460),     "kenya": (-1.2921, 36.8219),
    "kosovo": (42.6629, 21.1655),         "kuwait": (29.3759, 47.9774),
    "kyrgyzstan": (42.8746, 74.5698),     "laos": (17.9757, 102.6331),
    "latvia": (56.9496, 24.1052),         "lebanon": (33.8938, 35.5018),
    "libya": (32.9022, 13.1806),          "lithuania": (54.6872, 25.2797),
    "malaysia": (3.1390, 101.6869),       "mali": (12.6392, -8.0029),
    "mexico": (19.4326, -99.1332),        "moldova": (47.0105, 28.8638),
    "mongolia": (47.8864, 106.9057),      "morocco": (33.9716, -6.8498),
    "mozambique": (-25.9692, 32.5732),    "myanmar": (16.8661, 96.1951),
    "namibia": (-22.5594, 17.0832),       "nepal": (27.7172, 85.3240),
    "netherlands": (52.3676, 4.9041),     "nicaragua": (12.1150, -86.2362),
    "niger": (13.5137, 2.1098),           "nigeria": (9.0579, 7.4951),
    "north korea": (39.0392, 125.7625),   "north macedonia": (41.9981, 21.4254),
    "norway": (59.9139, 10.7522),         "oman": (23.6139, 58.5922),
    "pakistan": (33.6844, 73.0479),       "palestine": (31.9038, 35.2034),
    "panama": (8.9936, -79.5197),         "peru": (-12.0464, -77.0428),
    "philippines": (14.5995, 120.9842),   "poland": (52.2297, 21.0122),
    "portugal": (38.7169, -9.1395),       "qatar": (25.2854, 51.5310),
    "romania": (44.4268, 26.1025),        "russia": (55.7558, 37.6173),
    "rwanda": (-1.9441, 30.0619),         "saudi arabia": (24.7136, 46.6753),
    "senegal": (14.7167, -17.4677),       "serbia": (44.8176, 20.4633),
    "sierra leone": (8.4657, -13.2317),   "somalia": (2.0469, 45.3182),
    "south africa": (-25.7479, 28.2293),  "south korea": (37.5665, 126.9780),
    "south sudan": (4.8594, 31.5713),     "spain": (40.4168, -3.7038),
    "sri lanka": (6.9271, 79.8612),       "sudan": (15.5007, 32.5599),
    "sweden": (59.3293, 18.0686),         "switzerland": (46.9480, 7.4474),
    "syria": (33.5138, 36.2765),          "taiwan": (25.0330, 121.5654),
    "tajikistan": (38.5598, 68.7870),     "tanzania": (-6.1630, 35.7516),
    "thailand": (13.7563, 100.5018),      "tunisia": (36.8190, 10.1658),
    "turkey": (39.9334, 32.8597),         "turkmenistan": (37.9601, 58.3261),
    "uganda": (0.3476, 32.5825),          "ukraine": (50.4501, 30.5234),
    "united arab emirates": (24.4539, 54.3773),
    "united kingdom": (51.5074, -0.1278),
    "united states": (38.8951, -77.0364),
    "uruguay": (-34.9011, -56.1645),      "uzbekistan": (41.2995, 69.2401),
    "venezuela": (10.4806, -66.9036),     "vietnam": (21.0245, 105.8412),
    "yemen": (15.5527, 48.5164),          "zambia": (-15.4167, 28.2833),
    "zimbabwe": (-17.8252, 31.0335),
    # Major cities / regions often referenced in news
    "gaza": (31.5017, 34.4668),           "west bank": (31.9038, 35.2034),
    "crimea": (45.0000, 34.0000),         "donbas": (48.0159, 37.8028),
    "kashmir": (34.0837, 74.7973),        "xinjiang": (43.7930, 87.6270),
    "tibet": (29.6500, 91.1000),          "catalonia": (41.5912, 1.5209),
    "taiwan strait": (24.5000, 120.0000), "south china sea": (12.0000, 113.0000),
    "strait of hormuz": (26.5667, 56.2500),
}
# fmt: on


# ─── Nominatim geocoder (rate-limited to 1 req/sec by geopy) ──────────────────

_nominatim_factory: Any = Nominatim
_geolocator: Any = _nominatim_factory(
    user_agent=GEOCODE_USER_AGENT,
    timeout=GEOCODE_TIMEOUT,
)


@lru_cache(maxsize=512)
def _nominatim_lookup(query: str) -> Optional[Coord]:
    """Cached Nominatim lookup. Returns None on failure."""
    try:
        time.sleep(1.1)   # respect Nominatim's 1 req/sec policy
        location = _geolocator.geocode(query)
        if location:
            logger.debug("Nominatim resolved '%s' → (%.4f, %.4f)", query, location.latitude, location.longitude)
            return (location.latitude, location.longitude)
    except (GeocoderTimedOut, GeocoderServiceError) as exc:
        logger.warning("Nominatim error for '%s': %s", query, exc)
    return None


# ─── Sanity check for LLM-provided coordinates ────────────────────────────────

def _coords_are_plausible(lat: float, lon: float) -> bool:
    """Reject obviously hallucinated or zero-zero coordinates."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    if lat == 0.0 and lon == 0.0:
        return False
    return True


# ─── Public interface ─────────────────────────────────────────────────────────

def resolve_coordinates(
    country: str,
    city: Optional[str] = None,
    llm_lat: Optional[float] = None,
    llm_lon: Optional[float] = None,
) -> Coord:
    """
    Resolve the best available coordinates for a given country/city.

    Priority order:
      1. LLM-provided coords (if they pass the plausibility check)
      2. Nominatim geocoding of "city, country"
      3. Hardcoded capital lookup by country name
      4. Nominatim geocoding of country name alone
      5. (0, 0) last resort — will be filtered in display layer

    Args:
        country:  Country name string from LLM output.
        city:     Optional city name string from LLM output.
        llm_lat:  Latitude provided by the LLM.
        llm_lon:  Longitude provided by the LLM.

    Returns:
        (latitude, longitude) tuple.
    """
    # 1. Trust LLM coords if plausible
    if llm_lat is not None and llm_lon is not None:
        if _coords_are_plausible(llm_lat, llm_lon):
            return (llm_lat, llm_lon)
        logger.debug("LLM coords failed plausibility: (%.4f, %.4f)", llm_lat, llm_lon)

    country_key = country.strip().lower()
    city_str = city.strip() if city else ""

    # 2. Nominatim: city + country
    if city_str:
        query = f"{city_str}, {country}"
        result = _nominatim_lookup(query)
        if result:
            return result

    # 3. Hardcoded lookup
    if country_key in CAPITAL_COORDS:
        logger.debug("Hardcoded lookup matched: '%s'", country_key)
        return CAPITAL_COORDS[country_key]

    # Partial match on hardcoded table (e.g., "United States of America" → "united states")
    for key, coords in CAPITAL_COORDS.items():
        if key in country_key or country_key in key:
            logger.debug("Partial hardcoded match: '%s' → '%s'", country_key, key)
            return coords

    # 4. Nominatim: country alone
    result = _nominatim_lookup(country)
    if result:
        return result

    # 5. Last resort
    logger.warning("Could not resolve coordinates for '%s, %s'", city, country)
    return (0.0, 0.0)
