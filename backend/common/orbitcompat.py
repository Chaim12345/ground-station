# Copyright (c) 2026 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""OMM normalization and optional TLE export shared across orbit boundaries."""

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from sgp4 import omm
from sgp4.api import Satrec
from sgp4.exporter import export_tle


def normalize_omm_fields(raw_fields: Mapping[str, Any]) -> dict[str, str]:
    """Normalize persisted OMM fields for the Earth SGP4 implementation."""
    fields = {
        str(key).strip().upper(): str(value).strip()
        for key, value in raw_fields.items()
        if key is not None and value is not None and str(key).strip() and str(value).strip()
    }
    required = {
        "OBJECT_ID",
        "NORAD_CAT_ID",
        "EPOCH",
        "INCLINATION",
        "RA_OF_ASC_NODE",
        "ECCENTRICITY",
        "ARG_OF_PERICENTER",
        "MEAN_ANOMALY",
        "MEAN_MOTION",
    }
    missing = sorted(required - fields.keys())
    if missing:
        raise ValueError(f"OMM row missing required fields: {missing}")

    try:
        epoch = datetime.fromisoformat(fields["EPOCH"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid OMM epoch value: {fields['EPOCH']}") from error
    if epoch.tzinfo is None:
        # OMM's UTC epoch must never be interpreted in the host's local timezone.
        epoch = epoch.replace(tzinfo=timezone.utc)
    fields["EPOCH"] = (
        epoch.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.%f")
    )
    for name, default in {
        "CLASSIFICATION_TYPE": "U",
        "EPHEMERIS_TYPE": "0",
        "ELEMENT_SET_NO": "0",
        "REV_AT_EPOCH": "0",
        "MEAN_MOTION_DOT": "0.0",
        "MEAN_MOTION_DDOT": "0.0",
        "BSTAR": "0.0",
    }.items():
        fields.setdefault(name, default)
    return fields


def export_omm_tle(
    raw_fields: Mapping[str, Any], satellite_id: int
) -> tuple[Optional[str], Optional[str]]:
    """Export a compatibility cache only for five-digit catalogue numbers."""
    if satellite_id > 99999:
        return None, None
    fields = normalize_omm_fields(raw_fields)
    if int(fields["NORAD_CAT_ID"]) != satellite_id:
        raise ValueError("OMM NORAD_CAT_ID does not match the satellite record")
    satrec = Satrec()
    omm.initialize(satrec, fields)
    line1, line2 = export_tle(satrec)
    return line1.strip(), line2.strip()
