"""S6-04: the system is never indexed by search engines."""
import pytest

pytestmark = pytest.mark.django_db


def test_robots_txt_disallows_everything(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    assert response.content.decode() == "User-agent: *\nDisallow: /\n"


@pytest.mark.parametrize("url", ["/login/", "/robots.txt", "/healthz/", "/no-such-page/"])
def test_every_response_says_noindex(client, url):
    assert client.get(url)["X-Robots-Tag"] == "noindex, nofollow"


def test_login_page_has_robots_meta(client):
    assert '<meta name="robots" content="noindex, nofollow">' in client.get("/login/").content.decode()
