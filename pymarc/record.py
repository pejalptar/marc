# This file is part of pymarc. It is subject to the license terms in the
# LICENSE file found in the top-level directory of this distribution and at
# https://opensource.org/licenses/BSD-2-Clause. pymarc may be copied, modified,
# propagated, or distributed according to the terms contained in the LICENSE
# file.

"""Pymarc Record."""
from itertools import zip_longest
import json
import logging
import re
import unicodedata
import warnings


from pymarc.constants import DIRECTORY_ENTRY_LEN, END_OF_RECORD, LEADER_LEN
from pymarc.exceptions import (
    BadSubfieldCodeWarning,
    BaseAddressInvalid,
    BaseAddressNotFound,
    FieldNotFound,
    NoFieldsFound,
    RecordDirectoryInvalid,
    RecordLeaderInvalid,
    TruncatedRecord,
)
from pymarc.field import (
    END_OF_FIELD,
    SUBFIELD_INDICATOR,
    Field,
    RawField,
    map_marc8_field,
)
from pymarc.leader import Leader
from pymarc.marc8 import marc8_to_unicode


isbn_regex = re.compile(r"([0-9\-xX]+)")


class Record:
    """A class for representing a MARC record.

    Each Record object is made up of multiple Field objects. You'll probably want to look
    at the docs for :class:`Field <pymarc.record.Field>` to see how to fully use a Record
    object.

    Basic usage:

    .. code-block:: python

        field = Field(
            tag = '245',
            indicators = ['0','1'],
            subfields = [
                'a', 'The pragmatic programmer : ',
                'b', 'from journeyman to master /',
                'c', 'Andrew Hunt, David Thomas.',
            ])

        record.add_field(field)

    Or creating a record from a chunk of MARC in transmission format:

    .. code-block:: python

        record = Record(data=chunk)

    Or getting a record as serialized MARC21.

    .. code-block:: python

        raw = record.as_marc()

    You'll normally want to use a MARCReader object to iterate through
    MARC records in a file.
    """

    def __init__(
        self,
        data="",
        to_unicode=True,
        force_utf8=False,
        hide_utf8_warnings=False,
        utf8_handling="strict",
        leader=" " * LEADER_LEN,
        file_encoding="iso8859-1",
    ):
        """Initialize a Record."""
        self.leader = Leader(leader[0:10] + "22" + leader[12:20] + "4500")
        self.fields = list()
        self.pos = 0
        self.force_utf8 = force_utf8
        if len(data) > 0:
            self.decode_marc(
                data,
                to_unicode=to_unicode,
                force_utf8=force_utf8,
                hide_utf8_warnings=hide_utf8_warnings,
                utf8_handling=utf8_handling,
                encoding=file_encoding,
            )
        elif force_utf8:
            self.leader = self.leader[0:9] + "a" + self.leader[10:]

    def __str__(self):
        """Will return a prettified version of the record in MARCMaker format.

        See :func:`Field.__str__() <pymarc.record.Field.__str__>` for more information.
        """
        # join is significantly faster than concatenation
        text_list = ["=LDR  %s" % self.leader]
        text_list.extend([str(field) for field in self.fields])
        text = "\n".join(text_list) + "\n"
        return text

    def __getitem__(self, tag):
        """Allows a shorthand lookup by tag.

        .. code-block:: python

            record['245']
        """
        fields = self.get_fields(tag)
        if len(fields) > 0:
            return fields[0]
        return None

    def __contains__(self, tag):
        """Allows a shorthand test of tag membership.

        .. code-block:: python

            '245' in record
        """
        fields = self.get_fields(tag)
        return len(fields) > 0

    def __iter__(self):
        self.__pos = 0
        return self

    def __next__(self):
        if self.__pos >= len(self.fields):
            raise StopIteration
        self.__pos += 1
        return self.fields[self.__pos - 1]

    def add_field(self, *fields):
        """Add pymarc.Field objects to a Record object.

        Optionally you can pass in multiple fields.
        """
        self.fields.extend(fields)

    def add_grouped_field(self, *fields):
        """Add pymarc.Field objects to a Record object and sort them "grouped".

        Which means, attempting to maintain a loose numeric order per the MARC standard
        for "Organization of the record" (http://www.loc.gov/marc/96principl.html).
        Optionally you can pass in multiple fields.
        """
        for f in fields:
            if len(self.fields) == 0 or not f.tag.isdigit():
                self.fields.append(f)
                continue
            self._sort_fields(f, "grouped")

    def add_ordered_field(self, *fields):
        """Add pymarc.Field objects to a Record object and sort them "ordered".

        Which means, attempting to maintain a strict numeric order.
        Optionally you can pass in multiple fields.
        """
        for f in fields:
            if len(self.fields) == 0 or not f.tag.isdigit():
                self.fields.append(f)
                continue
            self._sort_fields(f, "ordered")

    def _sort_fields(self, field, mode):
        """Sort fields by `mode`."""
        if mode == "grouped":
            tag = int(field.tag[0])
        else:
            tag = int(field.tag)

        i, last_tag = 0, 0
        for selff in self.fields:
            i += 1
            if not selff.tag.isdigit():
                self.fields.insert(i - 1, field)
                break

            if mode == "grouped":
                last_tag = int(selff.tag[0])
            else:
                last_tag = int(selff.tag)

            if last_tag > tag:
                self.fields.insert(i - 1, field)
                break
            if len(self.fields) == i:
                self.fields.append(field)
                break

    def remove_field(self, *fields):
        """Remove one or more pymarc.Field objects from a Record object."""
        for f in fields:
            try:
                self.fields.remove(f)
            except ValueError:
                raise FieldNotFound

    def remove_fields(self, *tags):
        """Remove all the fields with the tags passed to the function.

        .. code-block:: python

            # remove all the fields marked with tags '200' or '899'.
            self.remove_fields('200', '899')
        """
        self.fields[:] = (field for field in self.fields if field.tag not in tags)

    def get_fields(self, *args):
        """Return a list of all the fields in a record tags matching `args`.

        .. code-block:: python

            title = record.get_fields('245')

        If no fields with the specified tag are found then an empty list is returned.
        If you are interested in more than one tag you can pass it as multiple arguments.

        .. code-block:: python

            subjects = record.get_fields('600', '610', '650')

        If no tag is passed in to get_fields() a list of all the fields will be
        returned.
        """
        if len(args) == 0:
            return self.fields

        return [f for f in self.fields if f.tag in args]

    def decode_marc(
        self,
        marc,
        to_unicode=True,
        force_utf8=False,
        hide_utf8_warnings=False,
        utf8_handling="strict",
        encoding="iso8859-1",
    ):
        """Populate the object based on the `marc`` record in transmission format.

        The Record constructor actually uses decode_marc() behind the scenes when you
        pass in a chunk of MARC data to it.
        """
        # extract record leader
        self.leader = marc[0:LEADER_LEN].decode("ascii")
        if len(self.leader) != LEADER_LEN:
            raise RecordLeaderInvalid

        if self.leader[9] == "a" or self.force_utf8:
            encoding = "utf-8"

        # extract the byte offset where the record data starts
        base_address = int(marc[12:17])
        if base_address <= 0:
            raise BaseAddressNotFound
        if base_address >= len(marc):
            raise BaseAddressInvalid
        if len(marc) < int(self.leader[:5]):
            raise TruncatedRecord

        # extract directory, base_address-1 is used since the
        # director ends with an END_OF_FIELD byte
        directory = marc[LEADER_LEN : base_address - 1].decode("ascii")

        # determine the number of fields in record
        if len(directory) % DIRECTORY_ENTRY_LEN != 0:
            raise RecordDirectoryInvalid
        field_total = len(directory) // DIRECTORY_ENTRY_LEN

        # add fields to our record using directory offsets
        field_count = 0
        while field_count < field_total:
            entry_start = field_count * DIRECTORY_ENTRY_LEN
            entry_end = entry_start + DIRECTORY_ENTRY_LEN
            entry = directory[entry_start:entry_end]
            entry_tag = entry[0:3]
            entry_length = int(entry[3:7])
            entry_offset = int(entry[7:12])
            entry_data = marc[
                base_address
                + entry_offset : base_address
                + entry_offset
                + entry_length
                - 1
            ]
            # assume controlfields are numeric; replicates ruby-marc behavior
            if entry_tag < "010" and entry_tag.isdigit():
                if to_unicode:
                    field = Field(tag=entry_tag, data=entry_data.decode(encoding))
                else:
                    field = RawField(tag=entry_tag, data=entry_data)
            else:
                subfields = list()
                subs = entry_data.split(SUBFIELD_INDICATOR.encode("ascii"))

                # The MARC spec requires there to be two indicators in a
                # field. However experience in the wild has shown that
                # indicators are sometimes missing, and sometimes there
                # are too many. Rather than throwing an exception because
                # we can't find what we want and rejecting the field, or
                # barfing on the whole record we'll try to use what we can
                # find. This means missing indicators will be recorded as
                # blank spaces, and any more than 2 are dropped on the floor.

                first_indicator = second_indicator = " "
                subs[0] = subs[0].decode("ascii")
                if len(subs[0]) == 0:
                    logging.warning("missing indicators: %s", entry_data)
                    first_indicator = second_indicator = " "
                elif len(subs[0]) == 1:
                    logging.warning("only 1 indicator found: %s", entry_data)
                    first_indicator = subs[0][0]
                    second_indicator = " "
                elif len(subs[0]) > 2:
                    logging.warning("more than 2 indicators found: %s", entry_data)
                    first_indicator = subs[0][0]
                    second_indicator = subs[0][1]
                else:
                    first_indicator = subs[0][0]
                    second_indicator = subs[0][1]

                for subfield in subs[1:]:
                    skip_bytes = 1
                    if len(subfield) == 0:
                        continue
                    try:
                        code = subfield[0:1].decode("ascii")
                    except UnicodeDecodeError:
                        warnings.warn(BadSubfieldCodeWarning())
                        code, skip_bytes = normalize_subfield_code(subfield)
                    data = subfield[skip_bytes:]

                    if to_unicode:
                        if self.leader[9] == "a" or force_utf8:
                            data = data.decode("utf-8", utf8_handling)
                        elif encoding == "iso8859-1":
                            data = marc8_to_unicode(data, hide_utf8_warnings)
                        else:
                            data = data.decode(encoding)
                    subfields.append(code)
                    subfields.append(data)
                if to_unicode:
                    field = Field(
                        tag=entry_tag,
                        indicators=[first_indicator, second_indicator],
                        subfields=subfields,
                    )
                else:
                    field = RawField(
                        tag=entry_tag,
                        indicators=[first_indicator, second_indicator],
                        subfields=subfields,
                    )
            self.add_field(field)
            field_count += 1

        if field_count == 0:
            raise NoFieldsFound

    def as_marc(self):
        """Returns the record serialized as MARC21."""
        fields = b""
        directory = b""
        offset = 0

        # build the directory
        # each element of the directory includes the tag, the byte length of
        # the field and the offset from the base address where the field data
        # can be found
        if self.leader[9] == "a" or self.force_utf8:
            encoding = "utf-8"
        else:
            encoding = "iso8859-1"

        for field in self.fields:
            field_data = field.as_marc(encoding=encoding)
            fields += field_data
            if field.tag.isdigit():
                directory += ("%03d" % int(field.tag)).encode(encoding)
            else:
                directory += ("%03s" % field.tag).encode(encoding)
            directory += ("%04d%05d" % (len(field_data), offset)).encode(encoding)

            offset += len(field_data)

        # directory ends with an end of field
        directory += END_OF_FIELD.encode(encoding)

        # field data ends with an end of record
        fields += END_OF_RECORD.encode(encoding)

        # the base address where the directory ends and the field data begins
        base_address = LEADER_LEN + len(directory)

        # figure out the length of the record
        record_length = base_address + len(fields)

        # update the leader with the current record length and base address
        # the lengths are fixed width and zero padded
        strleader = "%05d%s%05d%s" % (
            record_length,
            self.leader[5:12],
            base_address,
            self.leader[17:],
        )
        leader = strleader.encode(encoding)

        return leader + directory + fields

    # alias for backwards compatibility
    as_marc21 = as_marc

    def as_dict(self):
        """Turn a MARC record into a dictionary, which is used for ``as_json``."""
        record = {}
        record["leader"] = str(self.leader)
        record["fields"] = []
        for field in self:
            if field.is_control_field():
                record["fields"].append({field.tag: field.data})
            else:
                fd = {}
                fd["subfields"] = []
                fd["ind1"] = field.indicator1
                fd["ind2"] = field.indicator2
                for tag, value in zip_longest(*[iter(field.subfields)] * 2):
                    fd["subfields"].append({tag: value})
                record["fields"].append({field.tag: fd})
        return record  # as dict

    def as_json(self, **kwargs):
        """Serialize a record as JSON.

        See:
        http://dilettantes.code4lib.org/blog/2010/09/a-proposal-to-serialize-marc-in-json/
        """
        return json.dumps(self.as_dict(), **kwargs)

    def title(self):
        """Returns the title of the record (245 $a and $b)."""
        try:
            title = self["245"]["a"]
        except TypeError:
            title = None
        if title:
            try:
                title += " " + self["245"]["b"]
            except TypeError:
                pass
        return title

    def issn_title(self):
        """Returns the key title of the record (222 $a and $b)."""
        try:
            title = self["222"]["a"]
        except TypeError:
            title = None
        if title:
            try:
                title += " " + self["222"]["b"]
            except TypeError:
                pass
        return title

    def isbn(self):
        """Returns the first ISBN in the record or None if one is not present.

        The returned ISBN will be all numeric, except for an
        x/X which may occur in the checksum position.  Dashes and
        extraneous information will be automatically removed. If you need
        this information you'll want to look directly at the 020 field,
        e.g. record['020']['a']
        """
        try:
            isbn_number = self["020"]["a"]
            match = isbn_regex.search(isbn_number)
            if match:
                return match.group(1).replace("-", "")
        except TypeError:
            # ISBN not set
            pass
        return None

    def issn(self):
        """Returns the ISSN number [022]['a'] in the record or None."""
        try:
            return self["022"]["a"]
        except TypeError:
            return None

    def sudoc(self):
        """Returns a SuDoc classification number.

        Returns a Superintendent of Documents (SuDoc) classification number
        held in the 086 MARC tag. Classification number will be made up of
        a variety of dashes, dots, slashes, and colons. More information
        can be found at the following URL:
        https://www.fdlp.gov/file-repository/gpo-cataloging/1172-gpo-classification-manual
        """
        field = self["086"]
        return field.format_field() if field else None

    def author(self):
        """Returns the author from field 100, 110 or 111."""
        field = self["100"] or self["110"] or self["111"]
        return field.format_field() if field else None

    def uniformtitle(self):
        """Returns the uniform title from field 130 or 240."""
        field = self["130"] or self["240"]
        return field.format_field() if field else None

    def series(self):
        """Returns series fields.

        Note: 490 supersedes the 440 series statement which was both
        series statement and added entry. 8XX fields are added entries.
        """
        return self.get_fields("440", "490", "800", "810", "811", "830")

    def subjects(self):
        """Returns subjects fields.

        Note: Fields 690-699 are considered "local" added entry fields but
        occur with some frequency in OCLC and RLIN records.
        """
        # fmt: off
        return self.get_fields(
            "600", "610", "611", "630", "648", "650", "651", "653", "654", "655",
            "656", "657", "658", "662", "690", "691", "696", "697", "698", "699",
        )
        # fmt: on

    def addedentries(self):
        """Returns Added entries fields.

        Note: Fields 790-799 are considered "local" added entry fields but
        occur with some frequency in OCLC and RLIN records.
        """
        # fmt: off
        return self.get_fields(
            "700", "710", "711", "720", "730", "740", "752", "753", "754", "790",
            "791", "792", "793", "796", "797", "798", "799",
        )
        # fmt: on

    def location(self):
        """Returns location field (852)."""
        return self.get_fields("852")

    def notes(self):
        """Return notes fields (all 5xx fields)."""
        # fmt: off
        return self.get_fields(
            "500", "501", "502", "504", "505", "506", "507", "508", "510", "511",
            "513", "514", "515", "516", "518", "520", "521", "522", "524", "525",
            "526", "530", "533", "534", "535", "536", "538", "540", "541", "544",
            "545", "546", "547", "550", "552", "555", "556", "561", "562", "563",
            "565", "567", "580", "581", "583", "584", "585", "586", "590", "591",
            "592", "593", "594", "595", "596", "597", "598", "599",
        )
        # fmt: on

    def physicaldescription(self):
        """Return physical description fields (300)."""
        return self.get_fields("300")

    def publisher(self):
        """Return publisher from 260 or 264.

        Note: 264 field with second indicator '1' indicates publisher.
        """
        for f in self.get_fields("260", "264"):
            if self["260"]:
                return self["260"]["b"]
            if self["264"] and f.indicator2 == "1":
                return self["264"]["b"]

        return None

    def pubyear(self):
        """Returns publication year from 260 or 264."""
        for f in self.get_fields("260", "264"):
            if self["260"]:
                return self["260"]["c"]
            if self["264"] and f.indicator2 == "1":
                return self["264"]["c"]
        return None


def map_marc8_record(record):
    """Map MARC-8 record."""
    record.fields = [map_marc8_field(field) for field in record.fields]
    leader = list(record.leader)
    leader[9] = "a"  # see http://www.loc.gov/marc/specifications/speccharucs.html
    record.leader = "".join(leader)
    return record


def normalize_subfield_code(subfield):
    """Normalize subfield code."""
    skip_bytes = 1
    try:
        text_subfield = subfield.decode("utf-8")
        skip_bytes = len(text_subfield[0].encode("utf-8"))
    except UnicodeDecodeError:
        text_subfield = subfield.decode("latin-1")
    decomposed = unicodedata.normalize("NFKD", text_subfield)
    without_diacritics = decomposed.encode("ascii", "ignore").decode("ascii")
    return without_diacritics[0], skip_bytes
