"""Real records, copied (trimmed to the fields the parsers read) from the
live feeds on 2026-10-19 around 08:35 UTC: USGS 4.5_week, GDACS EVENTS4APP,
NASA EONET open events, NHC CurrentStorms and the GDELT export file
20261019084500. Tests build on these so the shapes match the real upstreams.
"""

from __future__ import annotations

import copy
from typing import Any

USGS: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": "us6000u3bi",
            "properties": {
                "mag": 5.4,
                "place": "184 km WNW of Port-Olry, Vanuatu",
                "time": 1792397558235,
                "updated": 1792398959699,
                "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us6000u3bi",
                "alert": None,
                "status": "reviewed",
                "tsunami": 0,
            },
            "geometry": {"type": "Point", "coordinates": [165.6383, -14.1387, 10]},
        },
        {
            "type": "Feature",
            "id": "us6000u3bc",
            "properties": {
                "mag": 5.9,
                "place": "185 km NW of Port-Olry, Vanuatu",
                "time": 1792396144903,
                "updated": 1792397705817,
                "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us6000u3bc",
                "alert": "green",
                "status": "reviewed",
                "tsunami": 0,
            },
            "geometry": {"type": "Point", "coordinates": [165.7054, -14.0261, 10]},
        },
    ],
}

GDACS: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [165.6383, -14.1387]},
            "properties": {
                "eventtype": "EQ",
                "eventid": 1572508,
                "episodeid": 1741100,
                "name": "Earthquake in Vanuatu",
                "htmldescription": "Green M 5.4 Earthquake in Vanuatu at: 19 Oct 2026 08:12:38.",
                "url": {
                    "report": "https://www.gdacs.org/report.aspx?eventid=1572508&episodeid=1741100&eventtype=EQ"
                },
                "alertlevel": "Green",
                "alertscore": 1,
                "iscurrent": "true",
                "country": "Vanuatu",
                "fromdate": "2026-10-19T08:12:38",
                "datemodified": "2026-10-19T08:33:42",
                "source": "NEIC",
                "polygonlabel": "Centroid",
                "severitydata": {
                    "severity": 5.4,
                    "severitytext": "Magnitude 5.4M, Depth:10km",
                    "severityunit": "M",
                },
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [138.4, 14.3]},
            "properties": {
                "eventtype": "TC",
                "eventid": 1001338,
                "episodeid": 20,
                "name": "Tropical Cyclone CHAMPI-26",
                "htmldescription": (
                    "Red Tropical Cyclone CHAMPI-26 in Guam, Northern Mariana Islands, Japan "
                    "from: 14 Oct 2026  to: 19 Oct 2026 ."
                ),
                "url": {
                    "report": "https://www.gdacs.org/report.aspx?eventid=1001338&episodeid=20&eventtype=TC"
                },
                "alertlevel": "Red",
                "alertscore": 3,
                "episodealertlevel": "Green",
                "iscurrent": "true",
                "country": "Guam, Northern Mariana Islands, Japan",
                "fromdate": "2026-10-14T12:00:00",
                "datemodified": "2026-10-19T08:01:01",
                "source": "JTWC",
                "polygonlabel": "Centroid",
                "severitydata": {
                    "severity": 277.776,
                    "severitytext": "Hurricane/Typhoon > 74 mph (maximum wind speed of 278 km/h)",
                    "severityunit": "km/h",
                },
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [2.1771, 41.3826]},
            "properties": {
                "eventtype": "FL",
                "eventid": 1104235,
                "episodeid": 2,
                "name": "Flood in Spain",
                "url": {
                    "report": "https://www.gdacs.org/report.aspx?eventid=1104235&episodeid=2&eventtype=FL"
                },
                "alertlevel": "Green",
                "alertscore": 1,
                "iscurrent": "true",
                "country": "Spain",
                "fromdate": "2026-10-17T01:00:00",
                "datemodified": "2026-10-19T06:04:45",
                "source": "GLOFAS",
                "polygonlabel": "Centroid",
                "severitydata": {
                    "severity": 0.0,
                    "severitytext": "Magnitude 0 ",
                    "severityunit": "",
                },
            },
        },
        {
            # The same flood's area polygon: an outline, not an event of its own.
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[2.0, 41.3], [2.3, 41.3], [2.3, 41.5], [2.0, 41.5], [2.0, 41.3]]],
            },
            "properties": {
                "eventtype": "FL",
                "eventid": 1104235,
                "episodeid": 2,
                "name": "Flood in Spain",
                "alertlevel": "Green",
                "iscurrent": "true",
                "fromdate": "2026-10-17T01:00:00",
                "polygonlabel": "Affected area",
            },
        },
    ],
}

EONET: dict[str, Any] = {
    "title": "EONET Events",
    "events": [
        {
            "id": "EONET_25340",
            "title": "Tropical Cyclone Wati",
            "description": None,
            "categories": [{"id": "severeStorms", "title": "Severe Storms"}],
            "sources": [
                {"id": "JTWC", "url": "https://www.metoc.navy.mil/jtwc/products/sh0127.tcw"}
            ],
            "geometry": [
                {
                    "magnitudeValue": 35.0,
                    "magnitudeUnit": "kts",
                    "date": "2026-10-16T06:00:00Z",
                    "type": "Point",
                    "coordinates": [-163.3, -10.8],
                },
                {
                    "magnitudeValue": 40.0,
                    "magnitudeUnit": "kts",
                    "date": "2026-10-16T18:00:00Z",
                    "type": "Point",
                    "coordinates": [-163.4, -12.7],
                },
            ],
        },
        {
            "id": "EONET_25310",
            "title": "Prescribed Fire Newman Piles RX, Coconino, Arizona",
            "description": None,
            "categories": [{"id": "wildfires", "title": "Wildfires"}],
            "sources": [
                {"id": "IRWIN", "url": "https://irwin.doi.gov/observer/incidents/2026-AZCOF-001566"}
            ],
            "geometry": [
                {
                    "magnitudeValue": 751.0,
                    "magnitudeUnit": "acres",
                    "date": "2026-10-14T10:42:00Z",
                    "type": "Point",
                    "coordinates": [-111.65, 35.016667],
                }
            ],
        },
    ],
}

NHC: dict[str, Any] = {
    "activeStorms": [
        {
            "id": "ep212026",
            "name": "Trudy",
            "classification": "TS",
            "intensity": "50",
            "pressure": "999",
            "latitudeNumeric": 19.7,
            "longitudeNumeric": -108,
            "movementDir": 280,
            "movementSpeed": 6,
            "lastUpdate": "2026-10-19T09:00:00.000Z",
            "publicAdvisory": {
                "advNum": "024",
                "url": "https://www.nhc.noaa.gov/text/MIATCPEP1.shtml",
            },
        }
    ]
}


def _gdelt(
    gid: str,
    day: str,
    code: str,
    geo_type: str,
    place: str,
    country: str,
    lat: str,
    lon: str,
    feature: str,
    url: str,
    mentions: str = "6",
) -> str:
    """A 61-column GDELT 2.0 export row with only the read columns filled."""
    cols = [""] * 61
    cols[0], cols[1] = gid, day
    cols[26], cols[27], cols[28] = code, code, code[:2]
    cols[31], cols[32], cols[33] = mentions, "1", mentions
    cols[51], cols[52], cols[53] = geo_type, place, country
    cols[56], cols[57], cols[58] = lat, lon, feature
    cols[59], cols[60] = "20261019084500", url
    return "\t".join(cols)


GDELT_ROWS: list[str] = [
    # Two articles' mentions of fighting in Mulhouse: one cluster, one outlet.
    _gdelt(
        "1328661305",
        "20261018",
        "190",
        "4",
        "Mulhouse, Alsace, France",
        "FR",
        "47.7381",
        "7.34793",
        "-1454215",
        "https://www.rte.ie/news/2026/1019/1595915-france-protests/",
        "7",
    ),
    _gdelt(
        "1328661306",
        "20261018",
        "190",
        "4",
        "Mulhouse, Alsace, France",
        "FR",
        "47.7381",
        "7.34793",
        "-1454215",
        "https://www.rte.ie/news/2026/1019/1595915-france-protests/",
        "1",
    ),
    # A fact-check about a FALSE protest claim, machine-coded as a protest in
    # New Delhi: why GDELT is never shown as confirmed.
    _gdelt(
        "1328661333",
        "20261019",
        "141",
        "4",
        "New Delhi, Delhi, India",
        "IN",
        "28.6",
        "77.2",
        "-2106102",
        "https://organiser.org/2026/10/19/417506/bharat/fact-check-image-from-spanish-protest-falsely-shared-as-anti-modi-protests-in-india/",
        "8",
    ),
    # A US city (geo type 3) is placed.
    _gdelt(
        "1328661457",
        "20261019",
        "193",
        "3",
        "Hollywood, California, United States",
        "US",
        "34.0983",
        "-118.327",
        "1660757",
        "https://gq.co.za/culture/entertainment/2-10-10-siyamthanda-bangani-is-one-to-watch-in-spinners-season-2/",
        "10",
    ),
    # A country centroid (geo type 1) is not placed.
    _gdelt(
        "1328661400",
        "20261019",
        "190",
        "1",
        "Afghanistan",
        "AF",
        "33",
        "66",
        "AF",
        "https://www.aol.co.uk/articles/tragedy-baby-among-six-migrants-080700000.html",
    ),
    # An economic-cooperation row (root 03) is not an incident.
    _gdelt(
        "1328661299",
        "20251019",
        "030",
        "4",
        "Warsaw, (PL67), Poland",
        "PL",
        "52.25",
        "21",
        "-534433",
        "https://www.933thedrive.com/2026/10/19/polish-satellite-maker/",
    ),
]

# 2026-10-19T09:00:00Z, just after the samples were taken.
NOW_MS = 1792400400000


def fresh(sample: dict[str, Any]) -> dict[str, Any]:
    """A deep copy a test may change."""
    return copy.deepcopy(sample)
