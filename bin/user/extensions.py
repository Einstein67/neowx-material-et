#
#    Copyright (c) 2009-2015 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#

"""User extensions module

This module is imported from the main executable, so anything put here will be
executed before anything else happens. This makes it a good place to put user
extensions.
"""

import locale
# This will use the locale specified by the environment variable 'LANG'
# Other options are possible. See:
# http://docs.python.org/2/library/locale.html#locale.setlocale
locale.setlocale(locale.LC_ALL, '')

import weewx.units

weewx.units.obs_group_dict['iaq'] = 'group_percent'

weewx.units.USUnits['group_iaq'] = 'count'
weewx.units.MetricUnits['group_iaq'] = 'count'
weewx.units.MetricWXUnits['group_iaq'] = 'count'
