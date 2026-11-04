import pytest

from lineagebox import dates, events, folders, settings


@pytest.mark.parametrize("value, expected", [
    ("2012:06:03 10:00:00", "2012-06-03T10:00:00"),
    (b"2012:06:03 10:00:00\x00", "2012-06-03T10:00:00"),
    ("2012-06-03 10:00", "2012-06-03T10:00:00"),
    ("2012:06:03", "2012-06-03T00:00:00"),
    ("0000:00:00 00:00:00", None),
    ("2035:10:31 10:08:51", None),  # a camera clock set in the future
    ("1970:01:01 00:00:00", None),
    ("1980:01:01 00:00:00", None),  # a camera that was never set
    ("1978:12:25 09:30:00", "1978-12-25T09:30:00"),  # a scanned print, with the day it was taken written in
    ("2012:13:40 10:00:00", None),
    ("", None),
    (None, None),
])
def test_parse_exif(value, expected):
    assert dates.parse_exif(value) == expected


@pytest.mark.parametrize("name, expected", [
    ("IMG_20160811_182239.jpg", "2016-08-11T18:22:39"),
    ("20180623_101500.jpg", "2018-06-23T10:15:00"),
    ("VID_20190816-120102.mp4", "2019-08-16T12:01:02"),
    ("2016-08-11 18.22.39.jpg", "2016-08-11T18:22:39"),
    ("PXL_20190816_123456789.jpg", "2019-08-16T00:00:00"),
    ("Screenshot_2019-08-16.png", "2019-08-16T00:00:00"),
    ("1985-07-04 picnic.jpg", "1985-07-04T00:00:00"),
    ("DSC_5000.NEF", None),
    ("IMG_1234.JPG", None),
    ("photo_20161399.jpg", None),
])
def test_from_filename(name, expected):
    assert dates.from_filename(name) == expected


@pytest.mark.parametrize("text, shown, first, last", [
    ("1987-08-14", "Aug 14, 1987", "1987-08-14", "1987-08-14"),
    ("8/14/1987", "Aug 14, 1987", "1987-08-14", "1987-08-14"),
    ("June 1987", "June 1987", "1987-06-01", "1987-06-30"),
    ("Summer of 1987", "summer 1987", "1987-06-01", "1987-08-31"),
    ("winter 1987", "winter 1987", "1987-12-01", "1988-02-29"),  # from that year's December: its Christmas
    ("summer/fall 1987", "between summer 1987 and fall 1987", "1987-06-01", "1987-11-30"),
    ("fall or winter 1987", "between fall 1987 and winter 1987", "1987-09-01", "1988-02-29"),  # the winter after
    ("summer 1987 or summer 1988", "between summer 1987 and summer 1988", "1987-06-01", "1988-08-31"),
    ("between 1986 and 1988", "between 1986 and 1988", "1986-01-01", "1988-12-31"),
    ("1986-1988", "between 1986 and 1988", "1986-01-01", "1988-12-31"),
    ("the 1980s", "1980s", "1980-01-01", "1989-12-31"),
    ("late 1980s", "late 1980s", "1987-01-01", "1989-12-31"),
    ("early 1987", "early 1987", "1987-01-01", "1987-04-30"),
    ("about 1987", "about 1987", "1986-01-01", "1988-12-31"),
    ("c. summer 1987", "about summer 1987", "1987-03-01", "1987-11-30"),
])
def test_photo_dates(text, shown, first, last):
    span = dates.photo_date(text)
    assert (span.text, span.first.isoformat(), span.last.isoformat()) == (shown, first, last)


def test_seasons_south_of_the_equator(con, cfg):
    summer = dates.photo_date("summer 1987", south=True)
    assert (summer.first.isoformat(), summer.last.isoformat()) == ("1987-12-01", "1988-02-29")
    assert dates.season_of(1, 1988, south=True) == ("summer", 1987) and dates.season_of(7, 1988) == ("summer", 1988)
    assert dates.season_of(1, 1988) == ("winter", 1987)
    assert not dates.south(con)
    con.execute("INSERT INTO search_fts(rowid, image_id, caption) VALUES (1, 1, 'x')")
    con.commit()
    settings.save(con, cfg, {"southern_hemisphere": "on"})
    assert dates.south(con)  # and the search's season words are made again on the worker's next pass
    assert con.execute("SELECT count(*) FROM search_fts").fetchone()[0] == 0


def test_photo_dates_that_cannot_be():
    assert dates.photo_date("  ") is None
    assert dates.photo_date("between summer 1987 and summer 1988").middle.isoformat() == "1988-01-15"
    for text, says in [("summer", "Can't read"), ("between 1990 and 1980", "comes after"), ("3000", "hasn't come"),
                       ("1700", "before 1800"), ("early summer 1987", "Can't read")]:
        with pytest.raises(dates.Problem, match=says):
            dates.photo_date(text)


def test_dates_in_numbers_can_be_day_first(con, cfg):
    assert events.parse_date("05/06/1990").sort == "1990-05-06"  # month first unless the Settings page says so
    settings.save(con, cfg, {"day_first": True})
    assert events.day_first(con) and events.parse_date("05/06/1990", events.day_first(con)).sort == "1990-06-05"
    assert dates.photo_date("25/06/1990", day_first=True).first.isoformat() == "1990-06-25"
    assert folders.regex_day("Party 5.6.98", day_first=True) == (1998, 6, 5)


@pytest.mark.parametrize("text, why", [("between 1960 and 1950", "comes after"), ("0000", "no year 0"),
                                       ("9999", "hasn't come yet"), ("0950", "from 1000")])
def test_event_dates_that_cant_be_right(con, text, why):
    pid = con.execute("INSERT INTO person(name) VALUES ('Ann')").lastrowid
    with pytest.raises(events.Problem, match=why):
        events.save(con, None, tag="BIRT", person_id=pid, date_text=text)
