# This file is part of pymarc. It is subject to the license terms in the
# LICENSE file found in the top-level directory of this distribution and at
# https://opensource.org/licenses/BSD-2-Clause. pymarc may be copied, modified,
# propagated, or distributed according to the terms contained in the LICENSE
# file.

import unittest

import pymarc

import pandas as pd


class RecordToSeriesTest(unittest.TestCase):
    """Tests converting a Record to a Pandas Series."""

    def setUp(self):
        with open("test/marc8.dat", "rb") as fh:
            reader = pymarc.MARCReader(fh, to_unicode=False)
            self.records = [r for r in reader]

    def test_record_to_series(self):
        series = pymarc.marcdf.record_to_series(self.records[0])
        self.assertEqual(type(series), pd.Series)


if __name__ == "__main__":
    unittest.main()
