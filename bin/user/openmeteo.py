#
# Copyright (c) 2025 Tomáš Filo <tfilosk@gmail.com>
#
# Distributed under the terms of the GNU GENERAL PUBLIC LICENSE
#

"""Extends the Cheetah generator search list to add weather forecast provided by open-meteo.com

[CheetahGenerator]
    search_list_extensions = user.openmeteo.Forecast

# Forecast behavior
# -------------------------------------------------------------------------
# Here you can change forecast behavior
#
[[Forecast]]
    # Forecast is provided by website https://open-meteo.com, all data available are under Attribution 4.0 International (CC BY 4.0) (https://creativecommons.org/licenses/by/4.0/)
    # By enabling forecast you must agree to Terms of use for Non-Commercial Use only. You can read full condition on website https://open-meteo.com/en/terms Please read more in Open Meteo terms of use.

    # IMPORTANT, READ BEFORE ENABLING FORECAST:
    # After installing skin you need to restart weewx service to make it work

    # To enable forecast add lowercase "forecast" without quotes to the values_order list above in the [[Appearance]] section

    # Timezone from list of database time zones, or auto will determine timezone from station coordinates
    timezone = auto

    # Forecast variables to display inside forecast cards, supported variables are:
    # temperature, evapotranspiration, precipitation, wind, uv, sun, uv-sun
    variables = temperature, evapotranspiration, precipitation, wind, uv, sun, uv-sun

    # Display weather icon yes/no
    show_icon = yes

    # Display forecast description yes/no
    show_description = yes

    # Number of days to show in forecast, min 1 and max 7
    days = 3

    # Model to use, by default "best_match" is used. For more options see https://open-meteo.com/en/docs only one model can be used at a time.
    model = best_match
"""

import time
from datetime import datetime, timedelta
import json
import hashlib
import urllib.request
import urllib.parse
import logging
from collections import Counter

from weewx.cheetahgenerator import SearchList
from weewx.units import ValueHelper, ValueTuple

VERSION = "1.0.3"

log = logging.getLogger(__name__)

# global cache variable, we will call api only once in hour
_weather_cache = {
    "params_hash": None,
    "data": None,
}

retries = 3
delay = 10  # seconds
timeout = 15  # seconds
supported_variables = [
    "temperature",
    "precipitation",
    "wind",
    "evapotranspiration",
    "uv",
    "sun",
    "uv-sun",
]  # supported variables


class Forecast(SearchList):

    def __init__(self, generator):
        SearchList.__init__(self, generator)
        log.info("version: %s" % VERSION)
        self.latitude = self.generator.stn_info.latitude_f
        self.longitude = self.generator.stn_info.longitude_f
        self.base_url = "https://api.open-meteo.com/v1/forecast"
        self.forecast_dict = generator.skin_dict.get("Extras", {}).get("Forecast", {})
        self.values_order = (
            generator.skin_dict.get("Extras", {})
            .get("Appearance", {})
            .get("values_order", [])
        )
        self.generator = generator

    def forecast(self):

        enabled = False
        if "forecast" in self.values_order:
            enabled = True

        if enabled != True:
            log.debug("Forecast is disabled")
            return None

        days_str = self.forecast_dict.get("days", "3")
        days = 3
        try:
            days = int(days_str)
        except (ValueError, TypeError):
            log.info(
                "Parameter days with value '%s' is not valid, 3 days will be used as fallback.",
                days_str,
            )
            days = 3

        if days < 1 or days > 7:
            log.info(
                "Parameter days with value '%s' is not valid, 3 days will be used as fallback.",
                days,
            )
            days = 3

        model = self.forecast_dict.get("model", "best_match")
        # check if model is string
        if not isinstance(model, str):
            log.info(
                "Parameter model with value '%s' is not valid, best_match will be used as fallback.",
                model,
            )
            model = "best_match"

        variables = self.forecast_dict.get("variables", [])
        # check if variables is list and if variables is string than convert to list
        if isinstance(variables, str):
            variables = [var.strip() for var in variables.split(",")]
        variables = [var for var in variables if var in supported_variables]
        log.debug("variables: %s", variables)

        # specify correct range of forecast, just to be sure it matches days in remap_data bellow
        now = datetime.now()
        
        today_date = now.date()
        today_midnight = datetime.combine(today_date, datetime.min.time())
        today = time.localtime(today_midnight.timestamp())
        start_date = time.strftime("%Y-%m-%d", today)

        target_date = now.date() + timedelta(days=days - 1)
        target_midnight = datetime.combine(target_date, datetime.min.time())
        target = time.localtime(target_midnight.timestamp())
        end_date = time.strftime("%Y-%m-%d", target)

        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "timezone": self.forecast_dict.get("timezone", "auto"),
            "start_date": start_date,
            "end_date": end_date,
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
            "et0_fao_evapotranspiration_unit": "mm",
            "timeformat": "unixtime",
            "models": model,
        }

        hourly = []
        daily = []
        hourly.append("weather_code")
        if "temperature" in variables:
            daily.append("temperature_2m_min")
            daily.append("temperature_2m_max")
        if "precipitation" in variables:
            daily.append("precipitation_sum")
            daily.append("precipitation_probability_max")
        if "wind" in variables:
            daily.append("wind_speed_10m_max")
            daily.append("wind_gusts_10m_max")
            daily.append("wind_direction_10m_dominant")
        if ("uv" in variables) or ("sun" in variables) or ("uv-sun" in variables):
            daily.append("uv_index_max")
            daily.append("sunshine_duration")
            daily.append("daylight_duration")
        if "evapotranspiration" in variables:
            daily.append("et0_fao_evapotranspiration")

        params["hourly"] = ",".join(hourly)
        params["daily"] = ",".join(daily)

        log.debug("params: %s", params)

        return fetch_forecast(self.generator, self.base_url, params, variables, now)


def hash_params(params):
    now = int(time.time())
    current_hour = str(now - (now % 3600))  # round to full hour
    params_strigified = (
        json.dumps(params, sort_keys=True) + current_hour
    )  # stringify params and add current hour and type to make sure we will call api only once in hour or if params are changed
    return hashlib.md5(
        params_strigified.encode()
    ).hexdigest()  # create hash from params


def fetch_forecast(generator, base_url, params, variables: list, now: datetime):
    global _weather_cache
    log.debug("Current cache state: %s", _weather_cache)
    params_hash = hash_params(params)
    if (
        _weather_cache["params_hash"] == params_hash
        and _weather_cache["data"] is not None
    ):
        log.debug("Using cached data.")
        return _weather_cache["data"]
    else:
        _weather_cache["params_hash"] = None
        _weather_cache["data"] = None
    for attempt in range(1, retries + 1):
        try:
            url = base_url + "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=timeout) as response:
                if response.status != 200:
                    raise Exception(f"HTTP error: {response.status}")
                body = response.read().decode("utf-8")
                log.debug("Fetched raw data: %s", body)
                data = json.loads(body)
                log.debug("Parsed raw data as json: %s", data)
                remaped_data = remap_data(generator, data, variables, now)
                # save to cache and return
                _weather_cache["params_hash"] = params_hash
                _weather_cache["data"] = remaped_data
                return _weather_cache["data"]
        except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
            if attempt < retries:
                log.debug("Retrying in %d seconds...", delay)
                time.sleep(delay)
            else:
                log.info(
                    "An exception occurred while fetching forecast data from api.open-meteo.com. Enable debug in weewx.conf logs for more details."
                )
                log.debug(e)
                return None
    return None


def remap_data(generator, data: dict, variables: list, now: datetime):
    # Using ValueHelper will enable easy converting to correct units set by user in weewx.conf
    def build_value_helper(value, unit, group):
        value_tuple = ValueTuple(value, unit, group)
        vh = ValueHelper(
            value_tuple, "current", generator.formatter, generator.converter
        )
        return vh

    try:
        # Calculate daily weather_codes per day by choosing code with highest occurence
        daily_weather_codes = []
        hourly_weather_codes = data.get("hourly", {}).get("weather_code", [])
        days = len(data.get("daily", {}).get("time", []))
        if len(hourly_weather_codes) != 24 * days:
            log.info(
                "Forecast data doesn't contain correct number of records for weather code. Skipping forecast and cleaning cached data."
            )
            _weather_cache["params_hash"] = None
            _weather_cache["data"] = None
            return None
        for n in range(days):
            hourly_weather_codes_for_day = hourly_weather_codes[n * 24 : (n + 1) * 24]
            counts = Counter(hourly_weather_codes_for_day)
            max_count = max(counts.values())
            candidates = [val for val, cnt in counts.items() if cnt == max_count]
            result = max(candidates)
            daily_weather_codes.append(result)

        log.debug("Calculated daily weather codes: %s", daily_weather_codes)

        # Remap daily values
        daily_list = []
        for i in range(days):
            # generate unix epoch time using python time module
            # tm_wday and tm_yday are autocalculated/fixed by time liberary
            # don't need to care about month length, time.mktime will handle it
            date = now.date() + timedelta(days=i)
            date_midnight = datetime.combine(date, datetime.min.time())
            midnight_timestamp = int(date_midnight.timestamp())
            dt = build_value_helper(midnight_timestamp, "unix_epoch", "group_time")
            daily_keys = {}
            daily_keys["weather_code"] = daily_weather_codes[i]
            if "temperature" in variables:
                daily_keys["temperature"] = {
                    "min": build_value_helper(
                        data["daily"].get("temperature_2m_min", [None])[i],
                        "degree_C",
                        "group_temperature",
                    ),
                    "max": build_value_helper(
                        data["daily"].get("temperature_2m_max", [None])[i],
                        "degree_C",
                        "group_temperature",
                    ),
                }
            if "precipitation" in variables:
                daily_keys["precipitation"] = {
                    "sum": build_value_helper(
                        data["daily"].get("precipitation_sum", [None])[i],
                        "mm",
                        "group_rain",
                    ),
                    "probability": build_value_helper(
                        data["daily"].get("precipitation_probability_max", [None])[i],
                        "percent",
                        "group_percent",
                    ),
                }
            if "evapotranspiration" in variables:
                daily_keys["et0_fao_evapotranspiration"] = build_value_helper(
                    data["daily"].get("et0_fao_evapotranspiration", [None])[i],
                    "mm",
                    "group_rain",
                )
            if ("sun" in variables) or ("uv-sun" in variables) or ("uv" in variables):
                daily_keys["sun"] = {
                    "sunshine_duration": build_value_helper(
                        data["daily"].get("sunshine_duration", [None])[i],
                        "second",
                        "group_deltatime",
                    ),
                    "daylight_duration": build_value_helper(
                        data["daily"].get("daylight_duration", [None])[i],
                        "second",
                        "group_deltatime",
                    ),
                    "uv": build_value_helper(
                        data["daily"].get("uv_index_max", [None])[i],
                        "uv_index",
                        "group_uv",
                    ),
                }
            if "wind" in variables:
                daily_keys["wind"] = {
                    "speed": build_value_helper(
                        data["daily"].get("wind_speed_10m_max", [None])[i],
                        "km_per_hour",
                        "group_speed",
                    ),
                    "direction": build_value_helper(
                        data["daily"].get("wind_direction_10m_dominant", [None])[i],
                        "degree_compass",
                        "group_direction",
                    ),
                    "gusts": build_value_helper(
                        data["daily"].get("wind_gusts_10m_max", [None])[i],
                        "km_per_hour",
                        "group_speed",
                    ),
                }

            daily_list.append([dt, daily_keys])

        remapped = {
            "daily": daily_list,
        }
        log.debug("Remapped data: %s", remapped)

        return remapped
    except BaseException as e:
        _weather_cache["params_hash"] = None
        _weather_cache["data"] = None
        log.error(
            "Error occured while processing forecast data, cleaning cached data. If this error occure again, please see detailed log below."
        )
        log.error(e)
    return None
