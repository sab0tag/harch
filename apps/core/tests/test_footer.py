"""Page footer: «Харчоблок · <hospital>, <city> · years», no © (the software is not the hospital's)."""
from datetime import date
from unittest import mock

import pytest

pytestmark = pytest.mark.django_db
LINE = "Харчоблок · КНП ОМР «ОХТИРСЬКА ЦРЛ», м. Охтирка · 2026"


@pytest.fixture(autouse=True)
def hospital(settings):
    settings.HOSPITAL_NAME = "КНП ОМР «ОХТИРСЬКА ЦРЛ»"
    settings.HOSPITAL_CITY = "м. Охтирка"


def test_footer_on_login_and_inside(client, login):
    assert LINE in client.get("/login/").content.decode()
    html = login("dept_nurse").get("/").content.decode()
    assert LINE in html and "©" not in html


def test_years_grow_by_themselves():
    from apps.core.context_processors import site

    with mock.patch("apps.core.context_processors.timezone.localdate", return_value=date(2028, 1, 5)):
        assert site(None)["FOOTER_YEARS"] == "2026–2028"
