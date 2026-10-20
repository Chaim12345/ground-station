# Copyright (c) 2025 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Planet-aware orbit service abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Mapping, Optional

from sgp4 import omm
from sgp4.api import Satrec
from sqlalchemy import select

import crud
from db.models import SatelliteOrbits, Satellites


class CentralBody(str, Enum):
    """Supported celestial bodies for orbit propagation."""

    EARTH = "earth"
    MOON = "moon"
    MARS = "mars"


class OrbitModelKind(str, Enum):
    """Supported orbit model kinds."""

    TLE = "tle"
    OMM = "omm"


@dataclass(frozen=True)
class OrbitState:
    """Normalized orbit state independent of source format."""

    object_id: int
    object_name: Optional[str]
    central_body: CentralBody
    model_kind: OrbitModelKind
    epoch: Optional[datetime]
    tle1: Optional[str]
    tle2: Optional[str]
    omm_payload: Optional[Dict[str, Any]]
    source: Optional[str]


@dataclass(frozen=True)
class PropagationInput:
    """Normalized input consumed by propagation functions.

    ``tle1`` and ``tle2`` are deliberately optional.  New GP catalogue entries
    can have six-digit catalogue numbers, which cannot be represented in TLE.
    ``satrec`` is initialized directly from OMM in that case.
    """

    object_id: int
    object_name: Optional[str]
    central_body: CentralBody
    model_kind: OrbitModelKind
    tle1: Optional[str]
    tle2: Optional[str]
    satrec: Optional[Satrec] = None


class OrbitServiceError(ValueError):
    """Raised when orbit data is missing or invalid."""


class UnsupportedOrbitConfigurationError(OrbitServiceError):
    """Raised when no propagator supports the requested central body/model."""


class OrbitPropagator(ABC):
    """Propagation adapter interface for central-body/model combinations."""

    @abstractmethod
    def supports(self, central_body: CentralBody, model_kind: OrbitModelKind) -> bool:
        """Whether this propagator can handle the given orbit configuration."""

    @abstractmethod
    def to_propagation_input(self, orbit_state: OrbitState) -> PropagationInput:
        """Transform orbit state into propagation input."""


class EarthTlePropagator(OrbitPropagator):
    """Native Earth/TLE propagation path."""

    def supports(self, central_body: CentralBody, model_kind: OrbitModelKind) -> bool:
        return central_body == CentralBody.EARTH and model_kind == OrbitModelKind.TLE

    def to_propagation_input(self, orbit_state: OrbitState) -> PropagationInput:
        tle1 = (orbit_state.tle1 or "").strip()
        tle2 = (orbit_state.tle2 or "").strip()
        if not tle1 or not tle2:
            raise OrbitServiceError(
                f"Missing TLE data for object_id={orbit_state.object_id} "
                f"(central_body={orbit_state.central_body.value}, model={orbit_state.model_kind.value})"
            )
        return PropagationInput(
            object_id=orbit_state.object_id,
            object_name=orbit_state.object_name,
            central_body=orbit_state.central_body,
            model_kind=orbit_state.model_kind,
            tle1=tle1,
            tle2=tle2,
            satrec=None,
        )


class EarthOmmPropagator(OrbitPropagator):
    """Earth OMM propagation path, initialized without a lossy TLE export."""

    def supports(self, central_body: CentralBody, model_kind: OrbitModelKind) -> bool:
        return central_body == CentralBody.EARTH and model_kind == OrbitModelKind.OMM

    def to_propagation_input(self, orbit_state: OrbitState) -> PropagationInput:
        if not orbit_state.omm_payload:
            raise OrbitServiceError(
                f"OMM row for object_id={orbit_state.object_id} has no OMM payload"
            )
        try:
            satrec = Satrec()
            omm.initialize(satrec, _normalize_omm_fields(orbit_state.omm_payload))
        except (KeyError, TypeError, ValueError) as e:
            raise OrbitServiceError(
                f"Invalid OMM data for object_id={orbit_state.object_id}: {e}"
            ) from e
        return PropagationInput(
            object_id=orbit_state.object_id,
            object_name=orbit_state.object_name,
            central_body=orbit_state.central_body,
            model_kind=orbit_state.model_kind,
            tle1=_coerce_optional_string(orbit_state.tle1),
            tle2=_coerce_optional_string(orbit_state.tle2),
            satrec=satrec,
        )


class OrbitService:
    """Central service for loading orbit state and producing propagation input."""

    def __init__(self, propagators: Optional[list[OrbitPropagator]] = None):
        self._propagators = propagators or [EarthTlePropagator(), EarthOmmPropagator()]

    async def get_orbit_state(
        self, dbsession, object_id: int, central_body: CentralBody
    ) -> OrbitState:
        if central_body != CentralBody.EARTH:
            raise UnsupportedOrbitConfigurationError(
                f"Central body '{central_body.value}' is not implemented yet"
            )

        satellite_result = await dbsession.execute(
            select(Satellites).where(Satellites.norad_id == object_id)
        )
        satellite_row = satellite_result.scalar_one_or_none()
        if satellite_row is None:
            raise OrbitServiceError(f"No satellite found for object_id={object_id}")

        orbit_result = await dbsession.execute(
            select(SatelliteOrbits).where(
                SatelliteOrbits.satellite_norad_id == object_id,
                SatelliteOrbits.central_body == central_body.value,
            )
        )
        orbit_row = orbit_result.scalar_one_or_none()
        if orbit_row is not None:
            return self.get_orbit_state_from_database_rows(satellite_row, orbit_row, central_body)

        # Fallback for legacy rows before canonical orbit backfill/migration.
        result = await crud.satellites.fetch_satellites(dbsession, norad_id=object_id)
        if not result.get("success") or not result.get("data"):
            raise OrbitServiceError(f"No satellite found for object_id={object_id}")
        if len(result["data"]) != 1:
            raise OrbitServiceError(
                f"Expected one satellite for object_id={object_id}, got {len(result['data'])}"
            )
        return self.get_orbit_state_from_satellite_record(result["data"][0], central_body)

    def get_orbit_state_from_satellite_record(
        self, satellite: Mapping[str, Any], central_body: CentralBody
    ) -> OrbitState:
        if central_body != CentralBody.EARTH:
            raise UnsupportedOrbitConfigurationError(
                f"Central body '{central_body.value}' is not implemented yet"
            )

        object_id_raw = satellite.get("norad_id")
        if object_id_raw is None:
            raise OrbitServiceError("Satellite record missing norad_id")
        try:
            object_id = int(object_id_raw)
        except (TypeError, ValueError) as e:
            raise OrbitServiceError(f"Invalid norad_id: {object_id_raw}") from e

        model_kind = _resolve_model_kind(satellite.get("orbit_format"))
        return OrbitState(
            object_id=object_id,
            object_name=_coerce_optional_string(satellite.get("name")),
            central_body=central_body,
            model_kind=model_kind,
            epoch=_coerce_datetime(satellite.get("orbit_epoch")),
            tle1=_coerce_optional_string(satellite.get("tle1")),
            tle2=_coerce_optional_string(satellite.get("tle2")),
            omm_payload=_coerce_optional_dict(satellite.get("orbit_payload")),
            source=_coerce_optional_string(satellite.get("source")),
        )

    def get_orbit_state_from_database_rows(
        self, satellite: Satellites, orbit: SatelliteOrbits, central_body: CentralBody
    ) -> OrbitState:
        if central_body != CentralBody.EARTH:
            raise UnsupportedOrbitConfigurationError(
                f"Central body '{central_body.value}' is not implemented yet"
            )
        if orbit.central_body != central_body.value:
            raise OrbitServiceError(
                f"Orbit row body mismatch for object_id={satellite.norad_id}: "
                f"expected={central_body.value} actual={orbit.central_body}"
            )

        model_kind = _resolve_model_kind(orbit.model_kind)
        return OrbitState(
            object_id=satellite.norad_id,
            object_name=_coerce_optional_string(satellite.name),
            central_body=central_body,
            model_kind=model_kind,
            epoch=_coerce_datetime(orbit.epoch),
            tle1=_coerce_optional_string(orbit.tle1),
            tle2=_coerce_optional_string(orbit.tle2),
            omm_payload=_coerce_optional_dict(orbit.omm_payload),
            source=_coerce_optional_string(satellite.source),
        )

    async def get_propagation_input(
        self, dbsession, object_id: int, central_body: CentralBody
    ) -> PropagationInput:
        orbit_state = await self.get_orbit_state(dbsession, object_id, central_body)
        return self.get_propagation_input_from_state(orbit_state)

    def get_propagation_input_from_satellite_record(
        self, satellite: Mapping[str, Any], central_body: CentralBody
    ) -> PropagationInput:
        orbit_state = self.get_orbit_state_from_satellite_record(satellite, central_body)
        return self.get_propagation_input_from_state(orbit_state)

    def get_propagation_input_from_state(self, orbit_state: OrbitState) -> PropagationInput:
        for propagator in self._propagators:
            if propagator.supports(orbit_state.central_body, orbit_state.model_kind):
                return propagator.to_propagation_input(orbit_state)
        raise UnsupportedOrbitConfigurationError(
            f"No propagator for central_body={orbit_state.central_body.value}, "
            f"model_kind={orbit_state.model_kind.value}"
        )


orbit_service = OrbitService()


def get_orbit_state(
    satellite: Mapping[str, Any], central_body: CentralBody = CentralBody.EARTH
) -> OrbitState:
    """Build orbit state from a satellite-like mapping."""
    return orbit_service.get_orbit_state_from_satellite_record(satellite, central_body)


async def get_orbit_state_for_object(
    dbsession, object_id: int, central_body: CentralBody = CentralBody.EARTH
) -> OrbitState:
    """Load orbit state for an object from database."""
    return await orbit_service.get_orbit_state(dbsession, object_id, central_body)


def get_propagation_input(
    satellite: Mapping[str, Any], central_body: CentralBody = CentralBody.EARTH
) -> PropagationInput:
    """Build propagation input from a satellite-like mapping."""
    return orbit_service.get_propagation_input_from_satellite_record(satellite, central_body)


async def get_propagation_input_for_object(
    dbsession, object_id: int, central_body: CentralBody = CentralBody.EARTH
) -> PropagationInput:
    """Load propagation input for an object from database."""
    return await orbit_service.get_propagation_input(dbsession, object_id, central_body)


def build_satellite_ephemeris_payload(
    satellite: Mapping[str, Any], central_body: CentralBody = CentralBody.EARTH
) -> Dict[str, Any]:
    """
    Build tracker ephemeris payload from normalized propagation input.

    TLE fields are retained only for legacy consumers.  OMM-capable consumers
    receive the canonical payload and must not depend on a TLE being present.
    """
    propagation_input = get_propagation_input(satellite, central_body=central_body)
    payload: Dict[str, Any] = {
        "norad_id": propagation_input.object_id,
        "name": propagation_input.object_name,
        "tle1": propagation_input.tle1,
        "tle2": propagation_input.tle2,
    }
    if propagation_input.model_kind == OrbitModelKind.OMM:
        # Use the satellite record field names: the worker feeds this payload
        # back through get_propagation_input() on every tracking iteration.
        payload["orbit_format"] = OrbitModelKind.OMM.value
        payload["orbit_payload"] = orbit_service.get_orbit_state_from_satellite_record(
            satellite, central_body
        ).omm_payload
    return payload


def build_skyfield_satellite(propagation_input: PropagationInput, timescale):
    """Build Skyfield's propagator from canonical OMM or legacy TLE input."""
    from skyfield.api import EarthSatellite

    if propagation_input.satrec is not None:
        satellite = EarthSatellite.from_satrec(propagation_input.satrec, timescale)
        satellite.name = propagation_input.object_name or str(propagation_input.object_id)
        return satellite

    if not propagation_input.tle1 or not propagation_input.tle2:
        raise OrbitServiceError(
            f"No usable propagation data for object_id={propagation_input.object_id}"
        )
    return EarthSatellite(
        propagation_input.tle1,
        propagation_input.tle2,
        name=propagation_input.object_name or str(propagation_input.object_id),
        ts=timescale,
    )


def _resolve_model_kind(raw_orbit_format: Any) -> OrbitModelKind:
    value = str(raw_orbit_format or "").strip().lower()
    return OrbitModelKind.OMM if value == OrbitModelKind.OMM.value else OrbitModelKind.TLE


def _coerce_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _coerce_optional_dict(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return dict(value)
    return None


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        normalized = normalized.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def _normalize_omm_fields(raw_fields: Mapping[str, Any]) -> Dict[str, str]:
    """Prepare persisted OMM fields for sgp4 without changing their meaning."""
    fields: Dict[str, str] = {}
    for key, value in raw_fields.items():
        if key is None or value is None:
            continue
        normalized_key = str(key).strip().upper()
        normalized_value = str(value).strip()
        if normalized_key and normalized_value:
            fields[normalized_key] = normalized_value

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
    missing = sorted(required - set(fields))
    if missing:
        raise ValueError(f"OMM row missing required fields: {missing}")

    epoch = _coerce_datetime(fields["EPOCH"])
    if epoch is None:
        raise ValueError(f"Invalid OMM epoch value: {fields['EPOCH']}")
    if epoch.tzinfo is None:
        # OMM timestamps without an offset are defined in the stated TIME_SYSTEM (UTC here),
        # never in the host machine's local timezone.
        epoch = epoch.replace(tzinfo=timezone.utc)
    fields["EPOCH"] = (
        epoch.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.%f")
    )
    fields.setdefault("CLASSIFICATION_TYPE", "U")
    fields.setdefault("EPHEMERIS_TYPE", "0")
    fields.setdefault("ELEMENT_SET_NO", "0")
    fields.setdefault("REV_AT_EPOCH", "0")
    fields.setdefault("MEAN_MOTION_DOT", "0.0")
    fields.setdefault("MEAN_MOTION_DDOT", "0.0")
    fields.setdefault("BSTAR", "0.0")
    return fields
