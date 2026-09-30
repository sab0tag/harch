"""Builds templates/help/kontur.html — the operating-loop diagrams (inline SVG from explicit coordinates).

Run after changing the workflow: python3 docs/kontur/build.py [standalone.html]
"""
from html import escape
from pathlib import Path


# ---------------------------------------------------------------- svg helpers
MARKERS = """<defs>
<marker id="{p}-fwd" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="m-fwd" d="M0 0L10 5L0 10z"/></marker>
<marker id="{p}-back" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="m-back" d="M0 0L10 5L0 10z"/></marker>
<marker id="{p}-sys" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="m-sys" d="M0 0L10 5L0 10z"/></marker>
<marker id="{p}-warn" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="m-warn" d="M0 0L10 5L0 10z"/></marker>
</defs>"""


class Svg:
    def __init__(self, prefix, w, h, label):
        self.p, self.w, self.h, self.label, self.parts = prefix, w, h, label, []

    def box(self, x, y, w, h, lines, kind="step"):
        self.parts.append(f'<rect class="b-{kind}" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>')
        n = len(lines)
        for i, ln in enumerate(lines):
            cls = "t-head" if i == 0 and n > 1 else "t-body"
            ty = y + h / 2 + (i - (n - 1) / 2) * 16 + 4.5
            self.parts.append(f'<text class="{cls}" x="{x + w / 2}" y="{ty:.1f}" text-anchor="middle">{escape(ln)}</text>')
        return (x, y, w, h)

    def diamond(self, cx, cy, w, h, lines):
        pts = f"{cx},{cy - h / 2} {cx + w / 2},{cy} {cx},{cy + h / 2} {cx - w / 2},{cy}"
        self.parts.append(f'<polygon class="b-decide" points="{pts}"/>')
        for i, ln in enumerate(lines):
            ty = cy + (i - (len(lines) - 1) / 2) * 15 + 4.5
            self.parts.append(f'<text class="t-body" x="{cx}" y="{ty:.1f}" text-anchor="middle">{escape(ln)}</text>')

    def path(self, d, kind="fwd", label=None, lx=None, ly=None, anchor="middle", dashed=False):
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.parts.append(f'<path class="e-{kind}" d="{d}" fill="none"{dash} marker-end="url(#{self.p}-{kind})"/>')
        if label:
            self.label_at(lx, ly, label, kind, anchor)

    def label_at(self, x, y, text, kind="fwd", anchor="middle"):
        self.parts.append(f'<text class="l-{kind}" x="{x}" y="{y}" text-anchor="{anchor}">{escape(text)}</text>')

    def raw(self, s):
        self.parts.append(s)

    def render(self):
        return (f'<svg viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{escape(self.label)}" '
                f'xmlns="http://www.w3.org/2000/svg">' + MARKERS.format(p=self.p) + "".join(self.parts) + "</svg>")


# ---------------------------------------------------------------- 1. swimlanes: the whole service day
def swimlanes():
    W, LW = 1240, 150
    lanes = [("sys", "Система", "планувальник"), ("nurse", "Старша медсестра", "кожне відділення"), ("diet", "Дієтсестра", "перевіряє все"),
             ("chef", "Шеф-кухар", "завідувач виробництва"), ("acc", "Бухгалтер", ""), ("admin", "Адміністратор", "")]
    top, LH = 64, 104
    H = top + LH * len(lanes) + 18
    s = Svg("sw", W, H, "Контур дня: замовлення від усіх відділень, план меню, кухня і списання — хто подає, хто затверджує, куди повертається")
    y = {k: top + i * LH for i, (k, _, _) in enumerate(lanes)}
    for i, (k, name, sub) in enumerate(lanes):
        s.raw(f'<rect class="{"lane-a" if i % 2 == 0 else "lane-b"}" x="0" y="{y[k]}" width="{W}" height="{LH}"/>')
        s.raw(f'<text class="lane-name" x="14" y="{y[k] + LH / 2 - 2}">{escape(name)}</text>')
        if sub:
            s.raw(f'<text class="lane-sub" x="14" y="{y[k] + LH / 2 + 15}">{escape(sub)}</text>')
    s.raw(f'<line class="lane-edge" x1="{LW}" y1="{top}" x2="{LW}" y2="{top + LH * len(lanes)}"/>')
    # time columns
    cols = [(LW + 10, "Д−1 · 00:05", "задачі створено"), (LW + 230, "Д−1 · до 12:00", "замовлення"), (LW + 450, "Д−1 · до 18:00", "план меню"),
            (LW + 690, "День Д · до 20:00", "кухня"), (LW + 900, "Д+1 · до 12:00", "списання")]
    for x, t, sub in cols:
        s.raw(f'<text class="col-time" x="{x}" y="26">{escape(t)}</text><text class="col-sub" x="{x}" y="44">{escape(sub)}</text>')
    bw, bh = 176, 58
    c = lambda k: y[k] + (LH - bh) / 2  # box top in lane
    # system
    s.box(LW + 10, c("sys"), bw, bh, ["Задача кожному", "«Замовлення відділення»"], "sys")
    s.box(LW + 400, c("sys"), 150, bh, ["Усі замовлення", "закриті → «План меню»"], "sys")
    s.box(LW + 640, c("sys"), 170, bh, ["Розхід заморожено", "→ «Приготування»"], "sys")
    s.box(LW + 870, c("sys"), 120, bh, ["→ «Списання»"], "sys")
    s.box(LW + 1010, c("sys"), 74, bh, ["День", "закрито"], "done")
    # nurse
    s.box(LW + 10, c("nurse"), bw, bh, ["Внести порції", "по дієтах → «Подати»"])
    # dietician
    s.box(LW + 230, c("diet"), 160, bh, ["Перевірити", "замовлення"], "check")
    s.box(LW + 430, c("diet"), 170, bh, ["Скласти план меню", "→ «Подати шеф-кухарю»"])
    s.box(LW + 690, c("diet"), 150, bh, ["Перевірити", "факт кухні"], "check")
    s.box(LW + 900, c("diet"), 140, bh, ["Затвердити", "списання"], "check")
    # chef
    s.box(LW + 450, c("chef"), 150, bh, ["Перевірити", "план меню"], "check")
    s.box(LW + 640, c("chef"), 190, bh, ["Готувати · внести факт", "→ «Подати дієтсестрі»"])
    # accountant
    s.box(LW + 880, c("acc"), 180, bh, ["Ціни · «Подати", "на звірку»"])
    # admin
    s.box(LW + 10, c("admin"), bw, bh, ["Скасувати замовлення", "(лише з причиною)"], "admin")
    s.box(LW + 420, c("admin"), 330, bh, ["Користувачі і ролі · журнал подій · ШІ-помічник", "бачить усе, власну подачу не затверджує"], "admin")
    mid = lambda k: y[k] + LH / 2
    # forward flow
    s.path(f"M{LW + 98} {c('sys') + bh} V{c('nurse')}", "sys", "нова задача", LW + 104, c("sys") + bh + 20, "start")
    s.path(f"M{LW + 10 + bw} {mid('nurse')} H{LW + 300} V{c('diet')}", "fwd", "подала", LW + 250, mid("nurse") - 7)
    s.path(f"M{LW + 390} {mid('diet') - 10} H{LW + 415} V{c('sys') + bh}", "fwd", "затвердила", LW + 409, mid("nurse") + 30, "end")
    s.path(f"M{LW + 475} {c('sys') + bh} V{c('diet')}", "sys")
    s.path(f"M{LW + 525} {c('diet') + bh} V{c('chef')}", "fwd", "подала", LW + 531, c("diet") + bh + 20, "start")
    s.path(f"M{LW + 600} {mid('chef') - 12} H{LW + 620} V{y['sys'] + 30} H{LW + 640}", "fwd", "затвердив", LW + 625, mid("chef") - 30, "start")
    s.path(f"M{LW + 735} {c('sys') + bh} V{c('chef')}", "sys")
    s.path(f"M{LW + 765} {c('chef')} V{c('diet') + bh}", "fwd", "подав", LW + 771, c("chef") - 12, "start")
    s.path(f"M{LW + 840} {mid('diet') - 10} H{LW + 855} V{y['sys'] + 30} H{LW + 870}", "fwd", "затвердила", LW + 845, y["sys"] + 22, "end")
    s.path(f"M{LW + 930} {c('sys') + bh} V{c('acc')}", "sys")
    s.path(f"M{LW + 990} {c('acc')} V{c('diet') + bh}", "fwd", "подав", LW + 996, c("acc") - 12, "start")
    s.path(f"M{LW + 1040} {mid('diet') - 10} H{LW + 1047} V{c('sys') + bh}", "fwd", "затвердила", LW + 1041, c("sys") + bh + 36, "end")
    # returns
    s.path(f"M{LW + 250} {c('diet')} V{mid('nurse') + 14} H{LW + 10 + bw}", "back", "повернула з коментарем", LW + 208, c("diet") - 10, "middle")
    s.path(f"M{LW + 470} {c('chef')} V{c('diet') + bh}", "back", "повернув", LW + 464, c("chef") - 12, "end")
    s.path(f"M{LW + 700} {c('diet') + bh} V{c('chef')}", "back", "повернула", LW + 694, c("diet") + bh + 20, "end")
    s.path(f"M{LW + 910} {c('diet') + bh} V{c('acc')}", "back", "повернула", LW + 904, c("diet") + bh + 20, "end")
    # admin cancel closes an order too
    s.path(f"M{LW + 10 + bw} {mid('admin')} H{LW + 405} V{c('sys') + bh + 4}", "sys", "скасоване = закрите", LW + 204, mid("admin") - 8, "start", dashed=True)
    return s.render()


# ---------------------------------------------------------------- 2. task life cycle (same for all four tasks)
def lifecycle():
    W, H = 980, 250
    s = Svg("lc", W, H, "Життєвий цикл будь-якої задачі: очікує, в роботі, на перевірці, затверджено або повернуто; скасувати може лише адміністратор до подачі")
    y0 = 70
    s.box(20, y0, 130, 54, ["Очікує", "pending"], "state")
    s.box(210, y0, 150, 54, ["В роботі", "документ редагується"], "state")
    s.box(430, y0, 150, 54, ["На перевірці", "лише читання"], "check")
    s.box(660, y0 - 48, 150, 54, ["Затверджено", "заблоковано назавжди"], "done")
    s.box(660, y0 + 60, 150, 54, ["Повернуто", "коментар обов'язковий"], "back")
    s.box(860, y0 - 48, 104, 54, ["Наступний", "етап дня"], "sys")
    s.path(f"M150 {y0 + 27} H210", "fwd", "відкрила", 180, y0 + 18)
    s.path(f"M360 {y0 + 27} H430", "fwd", "«Подати»", 395, y0 + 18)
    s.path(f"M580 {y0 + 14} H620 V{y0 - 21} H660", "fwd", "перевіряючий: ✓", 622, y0 - 30, "end")
    s.path(f"M580 {y0 + 40} H620 V{y0 + 87} H660", "back", "✗ + коментар", 614, y0 + 106, "end")
    s.path(f"M810 {y0 - 21} H860", "sys")
    s.path(f"M735 {y0 + 114} V{y0 + 150} H285 V{y0 + 54}", "back", "виправити і «Подати повторно»", 510, y0 + 166)
    s.box(20, y0 + 128, 130, 48, ["Скасовано", "день може йти далі"], "admin")
    s.path(f"M85 {y0 + 54} V{y0 + 128}", "sys", "адмін скасовує", 92, y0 + 86, "start", dashed=True)
    s.label_at(92, y0 + 101, "з причиною, до подачі", "sys", "start")
    return s.render()


# ---------------------------------------------------------------- 3. per-person flows
def role_flow(prefix, label, steps, checker, ok_to, back_to_step, extra_note=None):
    """steps: list of (title, sub) done by the maker; decision by checker; ✓ → ok_to; ✗ → back to steps[back_to_step]."""
    bw, bh, gap, x = 220, 50, 22, 24
    H = 20 + (len(steps) - 1) * (bh + gap) + bh + 50 + 70 + bh + 24
    s = Svg(prefix, 490, H, label)
    ys = []
    for i, (t, sub) in enumerate(steps):
        y = 20 + i * (bh + gap)
        ys.append(y)
        s.box(x, y, bw, bh, [t, sub] if sub else [t], "step" if i else "entry")
        if i:
            s.path(f"M{x + bw / 2} {ys[i - 1] + bh} V{y}", "fwd")
    dy = ys[-1] + bh + 50
    s.path(f"M{x + bw / 2} {ys[-1] + bh} V{dy - 30}", "fwd", "подано", x + bw / 2 + 6, ys[-1] + bh + 20, "start")
    s.diamond(x + bw / 2, dy, 190, 60, [checker, "перевіряє"])
    oy = dy + 70
    s.box(x, oy, bw, bh, ok_to, "done")
    s.path(f"M{x + bw / 2} {dy + 30} V{oy}", "fwd", "✓ затвердив(ла)", x + bw / 2 + 6, dy + 50, "start")
    by = ys[back_to_step] + bh / 2
    s.path(f"M{x + bw / 2 + 95} {dy} H{x + bw + 120} V{by} H{x + bw}", "back", "✗ повернув(ла)", x + bw + 126, dy - 8, "start")
    s.label_at(x + bw + 126, dy + 10, "з коментарем", "back", "start")
    s.label_at(x + bw + 20, by - 8, "виправити →", "back", "start")
    if extra_note:
        s.label_at(x + bw + 126, dy + 28, extra_note, "back", "start")
    return s.render()


def dietician_flow():
    W, H = 980, 470
    s = Svg("dt", W, H, "Дієтсестра: перевіряє замовлення, складає план для шеф-кухаря, перевіряє факт кухні і списання — що відбувається після ✓ і ✗")
    col = [20, 262, 504, 746]
    heads = [("1 · Замовлення", "до 12:00 Д−1"), ("2 · План меню", "до 18:00 Д−1"), ("3 · Факт кухні", "після 20:00 Д"), ("4 · Списання", "до 12:00 Д+1")]
    for x, (h, t) in zip(col, heads):
        s.raw(f'<text class="col-time" x="{x}" y="22">{escape(h)}</text><text class="col-sub" x="{x}" y="40">{escape(t)}</text>')
    bw, bh = 214, 50
    # 1 orders (checker)
    s.box(col[0], 60, bw, bh, ["Зведення замовлень", "усіх відділень"], "entry")
    s.diamond(col[0] + bw / 2, 175, 190, 60, ["Кожне замовлення", "перевірити"])
    s.path(f"M{col[0] + bw / 2} 110 V145", "fwd")
    s.box(col[0], 250, bw, bh, ["Затверджено", "(можна всі разом)"], "done")
    s.path(f"M{col[0] + bw / 2} 205 V250", "fwd", "✓", col[0] + bw / 2 + 8, 232, "start")
    s.box(col[0], 380, bw, bh, ["Медсестрі: червоний блок", "→ «Подати повторно»"], "back")
    s.path(f"M{col[0] + 12} 175 H{col[0] - 6} V405 H{col[0]}", "back")
    s.label_at(col[0] + 4, 350, "✗ + коментар", "back", "start")
    s.box(col[0], 315, bw, 44, ["усі закриті → план меню"], "sys")
    s.path(f"M{col[0] + bw / 2} 300 V315", "sys")
    # 2 plan (maker)
    s.box(col[1], 60, bw, bh, ["Меню для кожної дієти", "лише меню цієї дієти"], "entry")
    s.box(col[1], 130, bw, bh, ["Звірити ккал і БЖУ", "з нормою → «Зберегти»"])
    s.path(f"M{col[1] + bw / 2} 110 V130", "fwd")
    s.diamond(col[1] + bw / 2, 245, 190, 60, ["Шеф-кухар", "перевіряє"])
    s.path(f"M{col[1] + bw / 2} 180 V215", "fwd", "«Подати шеф-кухарю»", col[1] + bw / 2 - 6, 200, "end")
    s.box(col[1], 320, bw, bh, ["Розхід заморожено", "меню-розкладка PDF"], "done")
    s.path(f"M{col[1] + bw / 2} 275 V320", "fwd", "✓", col[1] + bw / 2 + 8, 302, "start")
    s.path(f"M{col[1] + bw / 2 + 95} 245 H{col[1] + bw + 10} V155 H{col[1] + bw}", "back")
    s.label_at(col[1] + bw + 4, 290, "✗ виправити", "back", "end")
    # 3 kitchen (checker)
    s.box(col[2], 60, bw, bh, ["Задача «Приготування»", "у «Мені перевірити»"], "entry")
    s.diamond(col[2] + bw / 2, 175, 190, 60, ["Факт vs план,", "причини відхилень"])
    s.path(f"M{col[2] + bw / 2} 110 V145", "fwd")
    s.box(col[2], 250, bw, bh, ["«Затвердити факт»", "→ задача «Списання»"], "done")
    s.path(f"M{col[2] + bw / 2} 205 V250", "fwd", "✓", col[2] + bw / 2 + 8, 232, "start")
    s.box(col[2], 330, bw, bh, ["Шеф-кухарю: виправити", "факт → подати повторно"], "back")
    s.path(f"M{col[2] + 12} 175 H{col[2] - 6} V355 H{col[2]}", "back")
    s.label_at(col[2] + 4, 318, "✗ + коментар", "back", "start")
    # 4 write-off (checker)
    s.box(col[3], 60, bw, bh, ["Списання від бухгалтера", "у «Мені перевірити»"], "entry")
    s.diamond(col[3] + bw / 2, 175, 190, 60, ["Кількості і ціни", "перевірити"])
    s.path(f"M{col[3] + bw / 2} 110 V145", "fwd")
    s.box(col[3], 250, bw, bh, ["«Затвердити списання»", "день закрито"], "done")
    s.path(f"M{col[3] + bw / 2} 205 V250", "fwd", "✓", col[3] + bw / 2 + 8, 232, "start")
    s.box(col[3], 330, bw, bh, ["Бухгалтеру: виправити", "→ «Подати повторно»"], "back")
    s.path(f"M{col[3] + 12} 175 H{col[3] - 6} V355 H{col[3]}", "back")
    s.label_at(col[3] + 4, 318, "✗ + коментар", "back", "start")
    return s.render()


def chef_flow():
    W, H = 980, 450
    s = Svg("cf", W, H, "Шеф-кухар: перевіряє план меню від дієтсестри, потім готує і подає фактичний розхід")
    bw, bh = 250, 50
    L, R = 20, 520
    s.raw('<text class="col-time" x="20" y="22">1 · Перевірити план</text><text class="col-sub" x="20" y="40">до 18:00 Д−1</text>')
    s.raw('<text class="col-time" x="520" y="22">2 · Кухня і факт</text><text class="col-sub" x="520" y="40">до 20:00 Д</text>')
    s.box(L, 60, bw, bh, ["План від дієтсестри", "у «Мені перевірити»"], "entry")
    s.diamond(L + bw / 2, 175, 200, 60, ["Склад меню", "і розхід"])
    s.path(f"M{L + bw / 2} 110 V145", "fwd")
    s.box(L, 250, bw, bh, ["«Затвердити план»", "розхід заморожено"], "done")
    s.path(f"M{L + bw / 2} 205 V250", "fwd", "✓", L + bw / 2 + 8, 232, "start")
    s.box(L, 330, bw, bh, ["«Повернути дієтсестрі»", "з коментарем"], "back")
    s.path(f"M{L + 25} 175 H{L - 6} V355 H{L}", "back")
    s.label_at(L + 4, 318, "✗", "back", "start")
    s.path(f"M{L + bw} 275 H{R - 30} V85 H{R}", "fwd", "друк меню-розкладки", L + bw + 8, 268, "start")
    s.box(R, 60, bw, bh, ["«Сьогодні на кухні»", "страви × порції"], "entry")
    s.box(R, 130, bw, bh, ["Факт у кг = план", "змінити лише відмінності"])
    s.path(f"M{R + bw / 2} 110 V130", "fwd")
    s.box(R, 200, bw, bh, ["Заміна продукту · причина", "якщо відхилення > 10%"])
    s.path(f"M{R + bw / 2} 180 V200", "fwd")
    s.diamond(R + bw / 2, 310, 200, 60, ["Дієтсестра", "перевіряє"])
    s.path(f"M{R + bw / 2} 250 V280", "fwd", "«Подати дієтсестрі»", R + bw / 2 + 6, 268, "start")
    s.box(R, 380, bw, bh, ["Факт затверджено", "→ бухгалтеру «Списання»"], "done")
    s.path(f"M{R + bw / 2} 340 V380", "fwd", "✓", R + bw / 2 + 8, 364, "start")
    s.path(f"M{R + bw / 2 + 100} 310 H{R + bw + 30} V155 H{R + bw}", "back")
    s.label_at(R + bw + 36, 240, "✗ виправити", "back", "start")
    return s.render()


def build():
    nurse = role_flow("nu", "Старша медсестра: подає порції свого відділення, дієтсестра затверджує або повертає з коментарем",
                      [("Вхід · «Мої задачі»", "або дзвіночок"), ("«Внести порції»", "замовлення на завтра"),
                       ("Порції по дієтах", "або «Пацієнтів немає»"), ("«Зберегти чернетку»", "можна повернутись")],
                      "Дієтсестра", ["Затверджено", "змінити вже не можна"], 2, "→ червоний блок")
    accountant = role_flow("ac", "Бухгалтер: вносить ціни і подає списання на звірку, дієтсестра затверджує або повертає",
                           [("Вхід · «Мої задачі»", "до 12:00 Д+1"), ("«Списати продукти»", "кількості з факту кухні"),
                            ("Ціни за кг", "вручну або «Завантажити з Excel»"), ("«Подати на звірку»", "ціни фіксуються")],
                           "Дієтсестра", ["Затверджено", "день закрито"], 2)
    html = TEMPLATE
    for k, v in {"%%SWIM%%": swimlanes(), "%%LIFE%%": lifecycle(), "%%NURSE%%": nurse, "%%DIET%%": dietician_flow(),
                 "%%CHEF%%": chef_flow(), "%%ACC%%": accountant}.items():
        html = html.replace(k, v)
    return html


def as_django_template(html):
    """The in-app page: vendored fonts, the real host, a way back to the instructions, a footer for staff."""
    fonts = html[html.index('<link rel="preconnect"'):html.index("<style>")]
    footer = html[html.index("<footer>"):html.index("</footer>") + len("</footer>")]
    html = (html.replace(fonts, "<link rel=\"stylesheet\" href=\"{% static 'vendor/fonts/kontur-fonts.css' %}\">\n")
            .replace("beyondthesignal.to/login", "{{ request.get_host }}/login")
            .replace("<header>\n", "<header>\n    <a class=\"back\" href=\"{% url 'help' %}\">← Інструкції</a>\n", 1)
            .replace(footer, "<footer>{{ hospital }} · схема відповідає роботі системи на 29.09.2026. "
                             "Строки подачі — налаштування за замовчуванням.</footer>")
            .replace("</style>", ".back{justify-self:start;font:500 14px var(--body);color:var(--go);text-decoration:none}\n"
                                 ".back:hover{text-decoration:underline}\n</style>"))
    head = '{% load static %}<!doctype html>\n<html lang="uk"><head><meta charset="utf-8">\n' \
           '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    return head + html.replace("</style>", "</style>\n</head><body>", 1) + "\n</body></html>\n"


TEMPLATE = Path(__file__).with_name("template.html").read_text()
OUT = Path(__file__).resolve().parents[2] / "templates" / "help" / "kontur.html"

if __name__ == "__main__":
    import sys

    page = build()
    OUT.write_text(as_django_template(page))
    print("written", OUT)
    if len(sys.argv) > 1:  # optional: the standalone page, e.g. for publishing outside the app
        Path(sys.argv[1]).write_text(page)
        print("written", sys.argv[1])
