# This file is part of pymarc. It is subject to the license terms in the
# LICENSE file found in the top-level directory of this distribution and at
# https://opensource.org/licenses/BSD-2-Clause. pymarc may be copied, modified,
# propagated, or distributed according to the terms contained in the LICENSE
# file.

"""From MARC21 to Pandas DataFrame and Series."""

from pandas import Series
from pymarc import Record


def record_to_series(record: Record) -> Series:
    """Converts a Record into a Pandas Series."""
    return Series(record.as_dict)
