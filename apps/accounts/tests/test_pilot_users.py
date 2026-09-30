"""S6-05: pilot staff from a spreadsheet, temporary passwords, forced change at the first sign-in."""
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from openpyxl import Workbook

from apps.accounts.models import Role, User
from apps.accounts.services import temporary_password, translit
from apps.accounts.tests.factories import DepartmentFactory, UserFactory

pytestmark = pytest.mark.django_db
EXAMPLE = Path(settings.BASE_DIR) / "docs" / "pilot_users.example.csv"


@pytest.fixture(autouse=True)
def therapy():
    return DepartmentFactory(name="Терапевтичне")


def run(*args):
    out = StringIO()
    call_command("create_pilot_users", *map(str, args), stdout=out)
    return out.getvalue()


def write_csv(tmp_path, text):
    path = tmp_path / "staff.csv"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("ukr, lat", [("Шевченко", "shevchenko"), ("Яковенко", "yakovenko"), ("Згурський", "zghurskyi"),
                                      ("Юрченко", "yurchenko"), ("Ткачук-Ільїна", "tkachukilina"), ("Мар'яна", "mariana")])
def test_transliteration_follows_the_official_table(ukr, lat):
    assert translit(ukr) == lat


def test_temporary_password_is_readable_and_valid():
    from django.contrib.auth.password_validation import validate_password

    pw = temporary_password()
    assert len(pw) == 12 and not set(pw) & set("0o1li")
    validate_password(pw)


def test_example_table_creates_everyone_with_temporary_passwords(therapy):
    output = run(EXAMPLE)
    assert "Створено 4, оновлено 0" in output
    nurse = User.objects.get(username="koval.m")
    assert nurse.role == Role.DEPT_NURSE and nurse.department == therapy and nurse.phone == "+380671234567"
    assert (nurse.last_name, nurse.first_name) == ("Коваль", "Марія Степанівна")
    chef = User.objects.get(username="petrenko.l")
    assert chef.role == Role.CHEF and chef.must_change_password
    password = next(line for line in output.splitlines() if "petrenko.l" in line).split("пароль: ")[1]
    assert chef.check_password(password)
    assert {"kovalenko.o", "shevchenko.i"} <= set(User.objects.values_list("username", flat=True))


def test_one_bad_row_changes_nothing(tmp_path):
    path = write_csv(tmp_path, "ПІБ;роль;відділення\nІваненко Ольга;Бухгалтер;\nСидоренко Анна;Старша медсестра;Кардіологічне\n"
                               "Бондар Ігор;Директор;\n")
    with pytest.raises(CommandError) as e:
        run(path)
    assert "Нічого не змінено" in str(e.value)
    assert "рядок 3: відділення «Кардіологічне» не знайдено (є: Терапевтичне)" in str(e.value)
    assert "рядок 4: невідома роль «Директор»" in str(e.value)
    assert not User.objects.filter(last_name="Іваненко").exists()


def test_existing_account_is_updated_and_keeps_its_password(tmp_path, therapy):
    old = UserFactory(username="nurse-therapy", role=Role.DEPT_NURSE, department=therapy)
    path = write_csv(tmp_path, "логін,ПІБ,роль,відділення\nnurse-therapy,Коваль Марія,Старша медсестра,Терапевтичне відділення\n")
    assert "Оновлено: nurse-therapy" in run(path)
    old.refresh_from_db()
    assert old.last_name == "Коваль" and old.check_password("pass12345!") and not old.must_change_password
    assert "пароль:" in run(path, "--reset-passwords")
    old.refresh_from_db()
    assert old.must_change_password and not old.check_password("pass12345!")


def test_generated_logins_never_collide(tmp_path):
    UserFactory(username="bondar.i")
    path = write_csv(tmp_path, "ПІБ;роль\nБондар Ігор;Бухгалтер\nБондар Іван;Адміністратор\n")
    run(path)
    assert {"bondar.i2", "bondar.i3"} <= set(User.objects.values_list("username", flat=True))


def test_dry_run_writes_nothing(tmp_path):
    output = run(EXAMPLE, "--dry-run")
    assert "ПЕРЕВІРКА, нічого не записано. Створено 4" in output
    assert not User.objects.filter(username="koval.m").exists()


def test_deactivate_others_switches_off_placeholder_accounts(therapy):
    UserFactory(username="nurse-therapy", role=Role.DEPT_NURSE, department=therapy)
    UserFactory(username="root", is_superuser=True, role=Role.ADMIN)
    assert "Вимкнено: nurse-therapy" in run(EXAMPLE, "--deactivate-others")
    assert not User.objects.get(username="nurse-therapy").is_active
    assert User.objects.get(username="root").is_active


def test_xlsx_and_password_file(tmp_path):
    wb = Workbook()
    wb.active.append(["ПІБ", "Роль", "Відділення", "Телефон"])
    wb.active.append(["Мельник Оксана", "старша медсестра", "терапевтичне", None])
    wb.save(tmp_path / "staff.xlsx")
    out = tmp_path / "passwords.csv"
    output = run(tmp_path / "staff.xlsx", "--out", out)
    assert "пароль:" not in output  # passwords go to the file only
    rows = out.read_text(encoding="utf-8-sig").splitlines()
    assert rows[0].startswith("Логін;ПІБ") and rows[1].startswith("melnyk.o;Мельник Оксана")
    assert User.objects.get(username="melnyk.o").check_password(rows[1].split(";")[-1])


# ------------------------------------------------------------------ first sign-in
def test_temporary_password_opens_only_the_password_page(client):
    user = UserFactory(role=Role.DIETICIAN, must_change_password=True)
    client.force_login(user)
    response = client.get("/orders/summary/")
    assert response.status_code == 302 and response.url == "/account/password/"
    htmx = client.get("/tasks/notifications/count/", HTTP_HX_REQUEST="true")
    assert htmx.status_code == 204 and htmx["HX-Redirect"] == "/account/password/"
    assert "тимчасовим паролем" in client.get("/account/password/").content.decode()
    response = client.post("/account/password/", {"old_password": "pass12345!", "new_password1": "Kvitka-Sumy-2026",
                                                  "new_password2": "Kvitka-Sumy-2026"})
    assert response.status_code == 302 and response.url == "/"
    user.refresh_from_db()
    assert not user.must_change_password and user.check_password("Kvitka-Sumy-2026")
    assert client.get("/").status_code == 200  # still signed in, work is open


def test_anyone_can_change_their_password_from_the_menu(login):
    client = login(Role.DEPT_NURSE)
    assert 'href="/account/password/"' in client.get("/").content.decode()
    assert client.get("/account/password/").status_code == 200


# ------------------------------------------------------------------ ops commands (ops/harch)
def test_temp_password_unlocks_and_forces_a_change(client):
    user = UserFactory(username="koval.m", role=Role.DIETICIAN, is_active=False)
    for _ in range(5):
        client.post("/login/", {"username": "koval.m", "password": "wrong"})
    out = StringIO()
    call_command("temp_password", "koval.m", stdout=out)
    password = out.getvalue().split("тимчасовий пароль: ")[1].strip()
    user.refresh_from_db()
    assert user.is_active and user.must_change_password and user.check_password(password)
    assert client.post("/login/", {"username": "koval.m", "password": password}).status_code == 302  # lock lifted


def test_temp_password_for_unknown_login():
    with pytest.raises(CommandError, match="немає користувача «nobody»"):
        call_command("temp_password", "nobody")


def test_list_users_shows_state():
    UserFactory(username="melnyk.o", role=Role.CHEF, must_change_password=True)
    UserFactory(username="gone", role=Role.CHEF, is_active=False)
    out = StringIO()
    call_command("list_users", stdout=out)
    assert "melnyk.o" in out.getvalue() and "тимчасовий пароль" in out.getvalue() and "gone" not in out.getvalue()
    out = StringIO()
    call_command("list_users", "--all", stdout=out)
    assert "gone" in out.getvalue() and "ВИМКНЕНО" in out.getvalue()


def test_substitution_login_links_to_the_main_one_in_any_order(tmp_path):
    """S7-06: the chef's second login as dietician names her main login — even above it in the table."""
    path = write_csv(tmp_path, "логін;ПІБ;роль;відділення;основний вхід\n"
                               "petrenko.l.diet;Петренко Лариса;Дієтсестра;;petrenko.l\n"
                               "petrenko.l;Петренко Лариса;Шеф-кухар;;\n")
    run(path)
    sub, main = User.objects.get(username="petrenko.l.diet"), User.objects.get(username="petrenko.l")
    assert sub.main_login == main and sub.role == Role.DIETICIAN and main.is_same_person(sub)
    bad = write_csv(tmp_path, "логін;ПІБ;роль;відділення;основний вхід\nx.y;Ікс Ігрек;Дієтсестра;;nobody\n")
    with pytest.raises(CommandError, match="основний вхід «nobody» не знайдено"):
        run(bad)
