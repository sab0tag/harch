"""Single source of truth for the left sidebar and for screen access (docs/04_ui.md).

Every item has a `key` and the roles that see it. Views declare access with
`roles_for("<key>")`, so a hidden menu link and a denied URL can never drift apart.
Items without `url_name` are screens planned for later sprints: they point to the generic
placeholder `/s/<key>/`, which enforces the same roles.
"""
from dataclasses import dataclass, field

from apps.accounts.models import Role

A, D, C, N, B = Role.ADMIN, Role.DIETICIAN, Role.CHEF, Role.DEPT_NURSE, Role.ACCOUNTANT
ALL = (A, D, C, N, B)

# Who may change reference data (admin always passes has_role).
CATALOG_EDITORS = (D,)
PRICE_EDITORS = (D, B)


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    icon: str
    roles: tuple[str, ...]
    url_name: str = ""          # real route, when the screen exists
    sprint: str = ""            # where a placeholder screen is planned
    url: str = ""               # absolute link (e.g. Django admin)

    @property
    def is_placeholder(self):
        return not self.url_name and not self.url


@dataclass(frozen=True)
class NavGroup:
    label: str                  # "" = top-level, no heading
    items: tuple[NavItem, ...] = field(default_factory=tuple)


NAV_GROUPS: tuple[NavGroup, ...] = (
    NavGroup("", (
        NavItem("home", "Головна", "house", ALL, url_name="home"),
        NavItem("tasks", "Мої задачі", "check2-square", ALL, sprint="Sprint 2"),
        NavItem("orders", "Замовлення", "clipboard-plus", (A, D, N), sprint="Sprint 3"),
        NavItem("plan", "План меню на день", "calendar-week", (A, D, C, B), sprint="Sprint 3"),
        NavItem("kitchen", "Сьогодні на кухні", "fire", (A, D, C), sprint="Sprint 4"),
        NavItem("consumption", "Розхід продуктів", "box-seam", (A, D, C, B), sprint="Sprint 4"),
        NavItem("write-off", "Списання", "receipt", (A, D, B), sprint="Sprint 4"),
        NavItem("reports", "Звіти", "bar-chart", (A, D, B), sprint="Sprint 4"),
    )),
    NavGroup("Довідники", (
        NavItem("dishes", "Картки-розкладки", "journal-text", (A, D, C, B), url_name="catalog:dish_list"),
        NavItem("menus", "Меню", "list-ol", (A, D, C), url_name="menus:list"),
        NavItem("products", "Продукти", "basket", (A, D, C, B), url_name="catalog:product_list"),
        NavItem("diets", "Дієти", "heart-pulse", (A, D, C), url_name="catalog:diet_list"),
        NavItem("departments", "Відділення", "hospital", (A, D), url_name="orgstructure:department_list"),
    )),
    NavGroup("Адміністрування", (
        NavItem("users", "Користувачі та ролі", "people", (A,), url="/admin/accounts/user/"),
        NavItem("audit", "Журнал подій", "clock-history", (A,), sprint="Sprint 2"),
    )),
)

ITEMS = {i.key: i for g in NAV_GROUPS for i in g.items}
PLACEHOLDERS = {k: i for k, i in ITEMS.items() if i.is_placeholder}


def roles_for(key: str) -> tuple[str, ...]:
    return ITEMS[key].roles


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
