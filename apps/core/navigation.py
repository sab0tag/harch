"""Single source of truth for the left sidebar (docs/04_ui.md, «Сайдбар по ролям»).

Every item declares which roles see it. Items whose screens are not built yet point to the
generic `section` placeholder, which enforces the same roles — so hiding a link and denying
the URL can never drift apart. When a real screen lands, replace `slug` with `url_name`.
"""
from dataclasses import dataclass, field

from apps.accounts.models import Role

A, D, C, N, B = Role.ADMIN, Role.DIETICIAN, Role.CHEF, Role.DEPT_NURSE, Role.ACCOUNTANT
ALL = (A, D, C, N, B)


@dataclass(frozen=True)
class NavItem:
    label: str
    icon: str
    roles: tuple[str, ...]
    url_name: str = ""          # real route, when the screen exists
    slug: str = ""              # placeholder route /s/<slug>/ until then
    sprint: str = ""            # where the real screen is planned
    url: str = ""               # absolute link (e.g. Django admin)


@dataclass(frozen=True)
class NavGroup:
    label: str                  # "" = top-level, no heading
    items: tuple[NavItem, ...] = field(default_factory=tuple)


NAV_GROUPS: tuple[NavGroup, ...] = (
    NavGroup("", (
        NavItem("Головна", "house", ALL, url_name="home"),
        NavItem("Мої задачі", "check2-square", ALL, slug="tasks", sprint="Sprint 2"),
        NavItem("Замовлення", "clipboard-plus", (A, D, N), slug="orders", sprint="Sprint 3"),
        NavItem("План меню на день", "calendar-week", (A, D, C, B), slug="plan", sprint="Sprint 3"),
        NavItem("Сьогодні на кухні", "fire", (A, D, C), slug="kitchen", sprint="Sprint 4"),
        NavItem("Розхід продуктів", "box-seam", (A, D, C, B), slug="consumption", sprint="Sprint 4"),
        NavItem("Списання", "receipt", (A, D, B), slug="write-off", sprint="Sprint 4"),
        NavItem("Звіти", "bar-chart", (A, D, B), slug="reports", sprint="Sprint 4"),
    )),
    NavGroup("Довідники", (
        NavItem("Картки-розкладки", "journal-text", (A, D, C, B), slug="dishes", sprint="Sprint 1"),
        NavItem("Меню", "list-ol", (A, D, C), slug="menus", sprint="Sprint 1"),
        NavItem("Продукти", "basket", (A, D, C, B), slug="products", sprint="Sprint 1"),
        NavItem("Дієти", "heart-pulse", (A, D, C), slug="diets", sprint="Sprint 1"),
        NavItem("Відділення", "hospital", (A, D), slug="departments", sprint="Sprint 1"),
    )),
    NavGroup("Адміністрування", (
        NavItem("Користувачі та ролі", "people", (A,), url="/admin/accounts/user/"),
        NavItem("Журнал подій", "clock-history", (A,), slug="audit", sprint="Sprint 2"),
    )),
)

PLACEHOLDERS = {i.slug: i for g in NAV_GROUPS for i in g.items if i.slug}


def visible_groups(user):
    """Groups with only the items `user` may see; empty groups dropped."""
    if not getattr(user, "is_authenticated", False):
        return []
    result = []
    for group in NAV_GROUPS:
        items = [i for i in group.items if user.has_role(*i.roles)]
        if items:
            result.append(NavGroup(group.label, tuple(items)))
    return result
