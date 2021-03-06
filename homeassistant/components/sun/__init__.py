"""Support for functionality to keep track of the sun."""
import logging
from datetime import timedelta

from homeassistant.const import (
    CONF_ELEVATION, SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET,
    EVENT_CORE_CONFIG_UPDATE)
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.sun import (
    get_astral_location, get_location_astral_event_next)
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'sun'

ENTITY_ID = 'sun.sun'

STATE_ABOVE_HORIZON = 'above_horizon'
STATE_BELOW_HORIZON = 'below_horizon'

STATE_ATTR_AZIMUTH = 'azimuth'
STATE_ATTR_ELEVATION = 'elevation'
STATE_ATTR_RISING = 'rising'
STATE_ATTR_NEXT_DAWN = 'next_dawn'
STATE_ATTR_NEXT_DUSK = 'next_dusk'
STATE_ATTR_NEXT_MIDNIGHT = 'next_midnight'
STATE_ATTR_NEXT_NOON = 'next_noon'
STATE_ATTR_NEXT_RISING = 'next_rising'
STATE_ATTR_NEXT_SETTING = 'next_setting'
STATE_ATTR_BRIGHTNESS = 'brightness'

# The algorithm used here is somewhat complicated. It aims to cut down
# the number of sensor updates over the day. It's documented best in
# the PR for the change, see the Discussion section of:
# https://github.com/home-assistant/home-assistant/pull/23832


# As documented in wikipedia: https://en.wikipedia.org/wiki/Twilight
# sun is:
# < -18° of horizon - all stars visible
PHASE_NIGHT = 'night'
# 18°-12° - some stars not visible
PHASE_ASTRONOMICAL_TWILIGHT = 'astronomical_twilight'
# 12°-6° - horizon visible
PHASE_NAUTICAL_TWILIGHT = 'nautical_twilight'
# 6°-0° - objects visible
PHASE_TWILIGHT = 'twilight'
# 0°-10° above horizon, sun low on horizon
PHASE_SMALL_DAY = 'small_day'
# > 10° above horizon
PHASE_DAY = 'day'

# 4 mins is one degree of arc change of the sun on its circle.
# During the night and the middle of the day we don't update
# that much since it's not important.
_PHASE_UPDATES = {
    PHASE_NIGHT: timedelta(minutes=4*5),
    PHASE_ASTRONOMICAL_TWILIGHT: timedelta(minutes=4*2),
    PHASE_NAUTICAL_TWILIGHT: timedelta(minutes=4*2),
    PHASE_TWILIGHT: timedelta(minutes=4),
    PHASE_SMALL_DAY: timedelta(minutes=2),
    PHASE_DAY: timedelta(minutes=4),
}


async def async_setup(hass, config):
    """Track the state of the sun."""
    if config.get(CONF_ELEVATION) is not None:
        _LOGGER.warning(
            "Elevation is now configured in home assistant core. "
            "See https://home-assistant.io/docs/configuration/basic/")
    Sun(hass)
    return True


class Sun(Entity):
    """Representation of the Sun."""

    entity_id = ENTITY_ID

    def __init__(self, hass):
        """Initialize the sun."""
        self.hass = hass
        self.location = None
        self._state = self.next_rising = self.next_setting = None
        self.next_dawn = self.next_dusk = None
        self.next_midnight = self.next_noon = None
        self.solar_elevation = self.solar_azimuth = None
        self.rising = self.phase = None
        self._next_change = None
        self.brightness = None

        def update_location(event):
            self.location = get_astral_location(self.hass)
            self.update_events(dt_util.utcnow())
        update_location(None)
        self.hass.bus.async_listen(
            EVENT_CORE_CONFIG_UPDATE, update_location)

    @property
    def name(self):
        """Return the name."""
        return "Sun"

    @property
    def state(self):
        """Return the state of the sun."""
        # 0.8333 is the same value as astral uses
        if self.solar_elevation > -0.833:
            return STATE_ABOVE_HORIZON

        return STATE_BELOW_HORIZON

    @property
    def state_attributes(self):
        """Return the state attributes of the sun."""
        return {
            STATE_ATTR_NEXT_DAWN: self.next_dawn.isoformat(),
            STATE_ATTR_NEXT_DUSK: self.next_dusk.isoformat(),
            STATE_ATTR_NEXT_MIDNIGHT: self.next_midnight.isoformat(),
            STATE_ATTR_NEXT_NOON: self.next_noon.isoformat(),
            STATE_ATTR_NEXT_RISING: self.next_rising.isoformat(),
            STATE_ATTR_NEXT_SETTING: self.next_setting.isoformat(),
            STATE_ATTR_ELEVATION: self.solar_elevation,
            STATE_ATTR_AZIMUTH: self.solar_azimuth,
            STATE_ATTR_RISING: self.rising,
            STATE_ATTR_BRIGHTNESS: self.brightness,
        }

    def _check_event(self, utc_point_in_time, event, before):
        next_utc = get_location_astral_event_next(
            self.location, event, utc_point_in_time)
        if next_utc < self._next_change:
            self._next_change = next_utc
            self.phase = before
        return next_utc

    @callback
    def update_events(self, utc_point_in_time):
        """Update the attributes containing solar events."""
        self._next_change = utc_point_in_time + timedelta(days=400)

        # Work our way around the solar cycle, figure out the next
        # phase. Some of these are stored.
        self.location.solar_depression = 'astronomical'
        self._check_event(utc_point_in_time, 'dawn', PHASE_NIGHT)
        self.location.solar_depression = 'nautical'
        self._check_event(
            utc_point_in_time, 'dawn', PHASE_ASTRONOMICAL_TWILIGHT)
        self.location.solar_depression = 'civil'
        self.next_dawn = self._check_event(
            utc_point_in_time, 'dawn', PHASE_NAUTICAL_TWILIGHT)
        self.next_rising = self._check_event(
            utc_point_in_time, SUN_EVENT_SUNRISE, PHASE_TWILIGHT)
        self.location.solar_depression = -10
        self._check_event(utc_point_in_time, 'dawn', PHASE_SMALL_DAY)
        self.next_noon = self._check_event(
            utc_point_in_time, 'solar_noon', None)
        self._check_event(utc_point_in_time, 'dusk', PHASE_DAY)
        self.next_setting = self._check_event(
            utc_point_in_time, SUN_EVENT_SUNSET, PHASE_SMALL_DAY)
        self.location.solar_depression = 'civil'
        self.next_dusk = self._check_event(
            utc_point_in_time, 'dusk', PHASE_TWILIGHT)
        self.location.solar_depression = 'nautical'
        self._check_event(
            utc_point_in_time, 'dusk', PHASE_NAUTICAL_TWILIGHT)
        self.location.solar_depression = 'astronomical'
        self._check_event(
            utc_point_in_time, 'dusk', PHASE_ASTRONOMICAL_TWILIGHT)
        self.next_midnight = self._check_event(
            utc_point_in_time, 'solar_midnight', None)
        self.location.solar_depression = 'civil'

        # if the event was solar midday or midnight, phase will now
        # be None. Solar noon doesn't always happen when the sun is
        # even in the day at the poles, so we can't rely on it.
        # Need to calculate phase if next is noon or midnight
        if self.phase is None:
            elevation = self.location.solar_elevation(self._next_change)
            if elevation >= 10:
                self.phase = PHASE_DAY
            elif elevation >= 0:
                self.phase = PHASE_SMALL_DAY
            elif elevation >= -6:
                self.phase = PHASE_TWILIGHT
            elif elevation >= -12:
                self.phase = PHASE_NAUTICAL_TWILIGHT
            elif elevation >= -18:
                self.phase = PHASE_ASTRONOMICAL_TWILIGHT
            else:
                self.phase = PHASE_NIGHT

        self.rising = self.next_noon < self.next_midnight

        _LOGGER.debug(
            "sun phase_update@%s: phase=%s",
            utc_point_in_time.isoformat(),
            self.phase,
        )
        self.update_sun_position(utc_point_in_time)

        # Set timer for the next solar event
        async_track_point_in_utc_time(
            self.hass, self.update_events,
            self._next_change)
        _LOGGER.debug("next time: %s", self._next_change.isoformat())

    @callback
    def update_sun_position(self, utc_point_in_time):
        """Calculate the position of the sun."""
        self.solar_azimuth = round(
            self.location.solar_azimuth(utc_point_in_time), 2)
        self.solar_elevation = round(
            self.location.solar_elevation(utc_point_in_time), 2)

        # jms: calculate brightness
        # cubic spline to azimuth:
        if self.solar_elevation >= 20:
            xl = (3.74, 3.97, -4.07, 1.47)
        elif self.solar_elevation >= 5:
            xl = (3.05, 13.28, -45.98, 64.33)
        elif self.solar_elevation >= -0.8:
            xl = (2.88, 22.26, -207.64, 1034.30)
        elif self.solar_elevation >= -5:
            xl = (2.88, 21.81, -258.11, -858.36)
        elif self.solar_elevation >= -12:
            xl = (2.70, 12.17, -431.69, -1899.83)
        else:
            xl=(0,0,0,0)
        x = self.solar_elevation / 90
        il = xl[0] + xl[1] * x + xl[2] * x * x + xl[3] * x * x * x  # 0.0 to 5.11
        self.brightness = max(0, round(il/5.11*100.0))  # adjust for percent

        _LOGGER.debug(
            "sun position_update@%s: elevation=%s azimuth=%s",
            utc_point_in_time.isoformat(),
            self.solar_elevation, self.solar_azimuth
        )
        self.async_write_ha_state()

        # Next update as per the current phase
        delta = _PHASE_UPDATES[self.phase]
        # if the next update is within 1.25 of the next
        # position update just drop it
        if utc_point_in_time + delta*1.25 > self._next_change:
            return
        async_track_point_in_utc_time(
            self.hass, self.update_sun_position,
            utc_point_in_time + delta)
